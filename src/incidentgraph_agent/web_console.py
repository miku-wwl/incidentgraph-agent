from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from incidentgraph_agent.action_evidence import (
    EvidenceSnapshotError,
    build_snapshot_from_agent_result,
)
from incidentgraph_agent.agent import AgentLoop, OllamaProvider, OllamaSettings
from incidentgraph_agent.config import Settings
from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import IncidentGraphError, IncompleteGraphEvidence
from incidentgraph_agent.evidence import EvidenceValidationError, validate_record
from incidentgraph_agent.graph_tools import GraphToolRegistry
from incidentgraph_agent.repository import IncidentRepository
from incidentgraph_agent.ticket_actions import (
    ActionWorkflowError,
    ActionWorkspace,
    action_status,
    admit_run,
    authorize_action,
    create_action_proposal,
    decide_run,
    execute_action,
    list_tickets,
    review_run,
)
from incidentgraph_agent.validation import validate_identifier

_RESULT_LIMIT = 500
_FETCH_LIMIT = _RESULT_LIMIT + 1
_MAX_BODY_BYTES = 16_384
_MAX_JOBS = 128
_MAX_INVESTIGATIONS = 16
_INVESTIGATION_TTL_SECONDS = 10 * 60
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_WEB_ROOT = Path(__file__).with_name("web")

_SERVICES = """
    MATCH (s:Service)
    RETURN s.id AS id, s.name AS name,
           s.environment AS environment, s.criticality AS criticality
    ORDER BY s.id LIMIT $limit
"""
_INCIDENTS = """
    MATCH (i:Incident)
    RETURN i.id AS id, i.started_at AS started_at,
           i.status AS status, i.summary AS summary
    ORDER BY i.id LIMIT $limit
"""
_DEPENDENCY_EDGES = """
    MATCH (a:Service)-[:DEPENDS_ON]->(b:Service)
    RETURN a.id AS source_id, b.id AS target_id
    ORDER BY a.id, b.id LIMIT $limit
"""
_INCIDENT_EDGES = """
    MATCH (i:Incident)-[:INVOLVES]->(s:Service)
    RETURN i.id AS incident_id, s.id AS service_id
    ORDER BY i.id, s.id LIMIT $limit
"""


class WebConsoleError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class _Job:
    job_id: str
    operation: str
    state: str = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "job_id": self.job_id,
            "operation": self.operation,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.result is not None:
            value["result"] = self.result
        if self.error is not None:
            value["error"] = self.error
        return value


def _uuid(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise WebConsoleError("invalid_request", f"{field_name} must be a UUID.")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise WebConsoleError(
            "invalid_request", f"{field_name} must be a UUID."
        ) from exc


def _identifier(value: Any, field_name: str) -> str:
    try:
        return validate_identifier(value, field=field_name)
    except IncidentGraphError as exc:
        raise WebConsoleError("invalid_request", f"{field_name} is invalid.") from exc


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _safe_error(exc: Exception) -> dict[str, str]:
    if isinstance(
        exc, (ActionWorkflowError, IncidentGraphError, EvidenceSnapshotError)
    ):
        code = getattr(exc, "code", "invalid_evidence")
        return {"code": str(code), "message": str(exc)[:500]}
    if isinstance(exc, WebConsoleError):
        return {"code": exc.code, "message": exc.args[0]}
    return {
        "code": "operation_failed",
        "message": "The local operation failed; no successful result is available.",
    }


def _rows(store: FalkorStore, query: str) -> list[Any]:
    response = store.read(query, {"limit": _FETCH_LIMIT})
    rows = getattr(response, "result_set", None)
    if not isinstance(rows, list):
        raise IncompleteGraphEvidence("FalkorDB returned an invalid graph result.")
    if len(rows) > _RESULT_LIMIT:
        raise IncompleteGraphEvidence(
            "The graph catalog exceeds the 500-row evidence limit; no partial graph was returned."
        )
    return rows


def _catalog(store: FalkorStore) -> dict[str, Any]:
    services: list[dict[str, str]] = []
    incidents: list[dict[str, str]] = []
    service_ids: set[str] = set()
    incident_ids: set[str] = set()
    for row in _rows(store, _SERVICES):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise IncompleteGraphEvidence("A service record could not be validated.")
        try:
            record = validate_record(
                "Service",
                dict(
                    zip(("id", "name", "environment", "criticality"), row, strict=True)
                ),
            )
        except EvidenceValidationError as exc:
            raise IncompleteGraphEvidence(
                "A service record is incomplete or invalid."
            ) from exc
        if record["id"] in service_ids:
            raise IncompleteGraphEvidence(
                "The graph contains duplicate service identities."
            )
        service_ids.add(record["id"])
        services.append(record)
    for row in _rows(store, _INCIDENTS):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise IncompleteGraphEvidence("An incident record could not be validated.")
        try:
            record = validate_record(
                "Incident",
                dict(zip(("id", "started_at", "status", "summary"), row, strict=True)),
            )
        except EvidenceValidationError as exc:
            raise IncompleteGraphEvidence(
                "An incident record is incomplete or invalid."
            ) from exc
        if record["id"] in incident_ids:
            raise IncompleteGraphEvidence(
                "The graph contains duplicate incident identities."
            )
        incident_ids.add(record["id"])
        incidents.append(record)

    edges: list[dict[str, str]] = []
    for row in _rows(store, _DEPENDENCY_EDGES):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise IncompleteGraphEvidence("A dependency edge could not be validated.")
        source = _identifier(row[0], "dependency source")
        target = _identifier(row[1], "dependency target")
        if source not in service_ids or target not in service_ids or source == target:
            raise IncompleteGraphEvidence("A dependency edge has an invalid endpoint.")
        edges.append({"source": source, "target": target, "type": "DEPENDS_ON"})
    if len({(edge["source"], edge["target"]) for edge in edges}) != len(edges):
        raise IncompleteGraphEvidence("The graph contains duplicate dependency edges.")

    associations: list[dict[str, str]] = []
    for row in _rows(store, _INCIDENT_EDGES):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise IncompleteGraphEvidence(
                "An incident association could not be validated."
            )
        incident_id = _identifier(row[0], "incident ID")
        service_id = _identifier(row[1], "incident service ID")
        if incident_id not in incident_ids or service_id not in service_ids:
            raise IncompleteGraphEvidence(
                "An incident association has an invalid endpoint."
            )
        associations.append(
            {"source": incident_id, "target": service_id, "type": "INVOLVES"}
        )
    if len({(edge["source"], edge["target"]) for edge in associations}) != len(
        associations
    ):
        raise IncompleteGraphEvidence(
            "The graph contains duplicate incident associations."
        )

    return {
        "services": sorted(services, key=lambda item: item["id"]),
        "incidents": sorted(incidents, key=lambda item: item["id"]),
        "dependencies": sorted(
            edges, key=lambda item: (item["source"], item["target"])
        ),
        "incident_associations": sorted(
            associations, key=lambda item: (item["source"], item["target"])
        ),
        "relationship_direction": "A DEPENDS_ON B means A requires B.",
        "evidence_complete": True,
    }


class _ActionHomeLock:
    """Prevent a second Web Console process from opening the same Runtime home."""

    def __init__(self, home: Path) -> None:
        self.path = home / ".incidentgraph-web.lock"
        self.file = None

    def acquire(self) -> None:
        if self.path.is_symlink():
            raise WebConsoleError(
                "unsafe_action_home", "The Web Console lock path is unsafe.", 409
            )
        self.file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0, os.SEEK_END)
                if self.file.tell() == 0:
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.file.close()
            self.file = None
            raise WebConsoleError(
                "action_home_in_use",
                "Another Web Console already owns this Action home. Stop it before continuing.",
                409,
            ) from exc

    def release(self) -> None:
        if self.file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None


class WebConsoleService:
    def __init__(
        self,
        home: str | Path,
        *,
        store_factory: Callable[[], FalkorStore] | None = None,
        provider_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.home = Path(home).expanduser().resolve()
        ActionWorkspace.load(self.home)
        self.store_factory = store_factory or (lambda: FalkorStore(Settings.from_env()))
        self.provider_factory = provider_factory or OllamaProvider
        self.operation_lock = threading.RLock()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="incidentgraph-job"
        )
        self._jobs: dict[str, _Job] = {}
        self._request_keys: dict[str, tuple[str, str]] = {}
        self._investigations: dict[
            str, tuple[float, dict[str, Any], dict[str, Any]]
        ] = {}
        self._action_ids_by_effect: dict[str, str] = {}
        self._closed = False

    @contextmanager
    def _store(self) -> Iterator[FalkorStore]:
        with self.store_factory() as store:
            yield store

    def health(self) -> dict[str, Any]:
        with self.operation_lock, self._store() as store:
            graph = store.verify()
        ollama = OllamaSettings.from_env()
        return {
            "status": "ready",
            "product": "IncidentGraph Agent",
            "model": ollama.model,
            "graph": graph,
            "action_home_ready": True,
        }

    def graph_catalog(self) -> dict[str, Any]:
        with self.operation_lock, self._store() as store:
            return _catalog(store)

    def dependencies(self, service_id: str) -> dict[str, Any]:
        service_id = _identifier(service_id, "service_id")
        with self.operation_lock, self._store() as store:
            return IncidentRepository(store).dependencies(service_id)

    def blast_radius(self, service_id: str) -> dict[str, Any]:
        service_id = _identifier(service_id, "service_id")
        with self.operation_lock, self._store() as store:
            return IncidentRepository(store).blast_radius(service_id)

    def investigation(self, investigation_id: str) -> dict[str, Any]:
        investigation_id = _uuid(investigation_id, "investigation_id")
        with self._lock:
            item = self._investigations.get(investigation_id)
            if item is None or time.monotonic() - item[0] > _INVESTIGATION_TTL_SECONDS:
                self._investigations.pop(investigation_id, None)
                raise WebConsoleError(
                    "investigation_expired",
                    "This investigation is no longer available; run it again before proposing an Action.",
                    404,
                )
            return item[2]

    def review(self, run_id: str) -> dict[str, Any]:
        run_id = _uuid(run_id, "run_id")
        with self.operation_lock:
            return review_run(self.home, run_id)

    def status(self, action_id: str) -> dict[str, Any]:
        action_id = _uuid(action_id, "action_id")
        with self.operation_lock:
            result = action_status(self.home, action_id)
            self._remember_action(result.get("action"))
            return result

    def tickets(self) -> dict[str, Any]:
        with self.operation_lock:
            result = list_tickets(self.home)
            with self._lock:
                for ticket in result["tickets"]:
                    action_id = self._action_ids_by_effect.get(ticket["effect_key"])
                    ticket["action_id"] = action_id
                    ticket["action_linked_by_effect_key"] = action_id is not None
            return result

    def _remember_action(self, action: Any) -> None:
        if not isinstance(action, dict):
            return
        action_id = action.get("action_id")
        effect_key = action.get("effect_key")
        if isinstance(action_id, str) and isinstance(effect_key, str):
            with self._lock:
                self._action_ids_by_effect[effect_key] = action_id

    def submit(
        self,
        request_id: str,
        operation: str,
        body: dict[str, Any],
        callback: Callable[[], dict[str, Any]],
    ) -> tuple[_Job, bool]:
        request_id = _uuid(request_id, "request_id")
        fingerprint = hashlib.sha256(
            _json_bytes({"operation": operation, "body": body})
        ).hexdigest()
        with self._lock:
            prior = self._request_keys.get(request_id)
            if prior is not None:
                prior_fingerprint, job_id = prior
                if prior_fingerprint != fingerprint:
                    raise WebConsoleError(
                        "request_id_conflict",
                        "This request ID was already used for a different operation.",
                        409,
                    )
                return self._jobs[job_id], True
            if self._closed:
                raise WebConsoleError(
                    "server_stopping", "The local server is stopping.", 503
                )
            if len(self._jobs) >= _MAX_JOBS:
                raise WebConsoleError(
                    "job_capacity_reached",
                    "The local job history is full; restart the Console after reviewing its current jobs.",
                    429,
                )
            job = _Job(str(uuid4()), operation)
            self._jobs[job.job_id] = job
            self._request_keys[request_id] = (fingerprint, job.job_id)
            self._executor.submit(self._run_job, job, callback)
            return job, False

    def job(self, job_id: str) -> dict[str, Any]:
        job_id = _uuid(job_id, "job_id")
        with self._lock:
            value = self._jobs.get(job_id)
            if value is None:
                raise WebConsoleError(
                    "job_not_found", "The requested job was not found.", 404
                )
            return value.as_dict()

    def _run_job(self, job: _Job, callback: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job.state = "running"
            job.started_at = _now()
        try:
            result = callback()
        except (
            ActionWorkflowError,
            IncidentGraphError,
            EvidenceSnapshotError,
            WebConsoleError,
        ) as exc:
            with self._lock:
                job.error = _safe_error(exc)
                job.state = "failed"
                job.finished_at = _now()
        except Exception:
            with self._lock:
                job.error = _safe_error(RuntimeError())
                job.state = "failed"
                job.finished_at = _now()
        else:
            with self._lock:
                job.result = result
                job.state = "complete"
                job.finished_at = _now()

    def start_investigation(self, incident_id: str) -> dict[str, Any]:
        incident_id = _identifier(incident_id, "incident_id")
        with self.operation_lock, self._store() as store:
            evidence = IncidentRepository(store).investigate(incident_id)
            settings = store.settings
            provider = self.provider_factory()
            try:
                result = AgentLoop(
                    provider, GraphToolRegistry(IncidentRepository(store))
                ).run(
                    f"Investigate {incident_id} and explain which services may be affected."
                )
            finally:
                close = getattr(provider, "close", None)
                if callable(close):
                    close()
            if result.get("status") != "complete":
                return _public_failed_investigation(result, incident_id)
            snapshot = build_snapshot_from_agent_result(
                result, incident_id=incident_id, graph_identity=settings.graph_name
            )
            investigation_id = str(uuid4())
            public = _public_investigation(result, investigation_id, snapshot, evidence)
            with self._lock:
                expired = [
                    key
                    for key, value in self._investigations.items()
                    if time.monotonic() - value[0] > _INVESTIGATION_TTL_SECONDS
                ]
                for key in expired:
                    self._investigations.pop(key, None)
                if len(self._investigations) >= _MAX_INVESTIGATIONS:
                    oldest = min(
                        self._investigations,
                        key=lambda key: self._investigations[key][0],
                    )
                    self._investigations.pop(oldest, None)
                self._investigations[investigation_id] = (
                    time.monotonic(),
                    snapshot,
                    public,
                )
            return public

    def propose(self, investigation_id: str) -> dict[str, Any]:
        investigation_id = _uuid(investigation_id, "investigation_id")
        with self._lock:
            item = self._investigations.get(investigation_id)
            if item is None or time.monotonic() - item[0] > _INVESTIGATION_TTL_SECONDS:
                self._investigations.pop(investigation_id, None)
                raise WebConsoleError(
                    "investigation_expired",
                    "Run a fresh investigation before proposing an Action.",
                    409,
                )
            snapshot = item[1]
        with self.operation_lock, self._store() as store:
            result = create_action_proposal(self.home, snapshot, graph_store=store)
        return result

    def decide(
        self, run_id: str, *, approve: bool, confirmation: str
    ) -> dict[str, Any]:
        run_id = _uuid(run_id, "run_id")
        with self.operation_lock, self._store() as store:
            return decide_run(
                self.home,
                run_id,
                approve=approve,
                confirm=lambda _challenge: confirmation,
                graph_store=store,
            )

    def admit(self, run_id: str) -> dict[str, Any]:
        run_id = _uuid(run_id, "run_id")
        with self.operation_lock, self._store() as store:
            result = admit_run(self.home, run_id, graph_store=store)
            self._remember_action(result.get("action"))
            return result

    def authorize(self, action_id: str, confirmation: str) -> dict[str, Any]:
        action_id = _uuid(action_id, "action_id")
        with self.operation_lock, self._store() as store:
            result = authorize_action(
                self.home,
                action_id,
                confirm=lambda _challenge: confirmation,
                graph_store=store,
            )
            self._remember_action(result.get("action"))
            return result

    def execute(self, action_id: str, confirmation: str) -> dict[str, Any]:
        action_id = _uuid(action_id, "action_id")
        with self.operation_lock, self._store() as store:
            result = execute_action(
                self.home,
                action_id,
                confirm=lambda _challenge: confirmation,
                graph_store=store,
            )
            self._remember_action(result.get("action"))
            return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)


class _ConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self, port: int, service: WebConsoleService, home_lock: _ActionHomeLock
    ):
        self.service = service
        self.home_lock = home_lock
        super().__init__(("127.0.0.1", port), _Handler)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.service.close()
            self.home_lock.release()


class _Handler(BaseHTTPRequestHandler):
    server: _ConsoleHTTPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _headers(
        self, *, content_type: str, length: int, no_store: bool = False
    ) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("Cache-Control", "no-store" if no_store else "no-cache")

    def _json(self, status: int, value: Any) -> None:
        data = _json_bytes(value)
        self.send_response(status)
        self._headers(
            content_type="application/json; charset=utf-8",
            length=len(data),
            no_store=True,
        )
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _host_ok(self) -> bool:
        expected = f"127.0.0.1:{self.server.server_address[1]}"
        return self.headers.get("Host", "").lower() == expected.lower()

    def _fetch_site_ok(self) -> bool:
        value = self.headers.get("Sec-Fetch-Site")
        return value is None or value.lower() in {"same-origin", "none"}

    def _origin_ok(self) -> bool:
        expected = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self.headers.get("Origin") == expected

    def do_GET(self) -> None:
        if not self._host_ok() or not self._fetch_site_ok():
            self._error(403, "origin_rejected", "The request origin is not allowed.")
            return
        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/style.css":
            self._static("style.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
            return
        try:
            result = self._get_api(
                parsed.path, parse_qs(parsed.query, strict_parsing=True)
            )
            self._json(200, result)
        except WebConsoleError as exc:
            self._error(exc.status, exc.code, str(exc))
        except (ActionWorkflowError, IncidentGraphError, EvidenceSnapshotError) as exc:
            payload = _safe_error(exc)
            status = (
                503
                if isinstance(exc, IncidentGraphError)
                and exc.code in {"database_unavailable", "model_unavailable"}
                else 400
            )
            self._error(status, payload["code"], payload["message"])
        except ValueError:
            self._error(400, "invalid_request", "The request query is invalid.")
        except Exception:
            self._error(500, "operation_failed", "The local read operation failed.")

    def do_POST(self) -> None:
        if (
            not self._host_ok()
            or not self._origin_ok()
            or not self._fetch_site_ok()
            or self.headers.get("X-IncidentGraph-Console") != "1"
        ):
            self._error(
                403, "origin_rejected", "The write request origin is not allowed."
            )
            return
        content_type = (
            self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "application/json" or self.headers.get("Transfer-Encoding"):
            self._error(
                415, "json_required", "Write requests must use bounded JSON bodies."
            )
            return
        try:
            length_text = self.headers.get("Content-Length", "")
            if not length_text.isascii() or not length_text.isdigit():
                raise WebConsoleError("invalid_request", "Content-Length is invalid.")
            length = int(length_text)
            if length > _MAX_BODY_BYTES:
                raise WebConsoleError(
                    "request_too_large", "The request body is too large.", 413
                )
            body = json.loads(self.rfile.read(length))
            if type(body) is not dict:
                raise WebConsoleError(
                    "invalid_request", "The JSON body must be an object."
                )
            request_id = _uuid(body.get("request_id"), "request_id")
            operation, callback = self._post_operation(urlsplit(self.path).path, body)
            job, duplicate = self.server.service.submit(
                request_id, operation, body, callback
            )
            self._json(
                200 if duplicate else 202,
                {"duplicate": duplicate, "job": job.as_dict()},
            )
        except WebConsoleError as exc:
            self._error(exc.status, exc.code, str(exc))
        except (ActionWorkflowError, IncidentGraphError, EvidenceSnapshotError) as exc:
            payload = _safe_error(exc)
            self._error(400, payload["code"], payload["message"])
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(
                400, "invalid_json", "The request body is not valid UTF-8 JSON."
            )
        except Exception:
            self._error(
                500, "operation_failed", "The local write request failed safely."
            )

    def _static(self, name: str, content_type: str) -> None:
        path = _WEB_ROOT / name
        try:
            data = path.read_bytes()
        except OSError:
            self._error(
                500, "static_asset_missing", "A local Web Console asset is unavailable."
            )
            return
        self.send_response(200)
        self._headers(content_type=content_type, length=len(data))
        self.end_headers()
        self.wfile.write(data)

    def _get_api(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        service = self.server.service
        if path == "/api/health" and not query:
            return service.health()
        if path == "/api/catalog" and not query:
            return service.graph_catalog()
        if path in {"/api/dependencies", "/api/blast-radius"}:
            service_id = _single_query(query, "service_id")
            return (
                service.dependencies(service_id)
                if path == "/api/dependencies"
                else service.blast_radius(service_id)
            )
        if path.startswith("/api/jobs/") and not query:
            return service.job(path.removeprefix("/api/jobs/"))
        if path.startswith("/api/investigations/") and not query:
            return service.investigation(path.removeprefix("/api/investigations/"))
        if path == "/api/review":
            return service.review(_single_query(query, "run_id"))
        if path == "/api/status":
            return service.status(_single_query(query, "action_id"))
        if path == "/api/tickets" and not query:
            return service.tickets()
        raise WebConsoleError(
            "not_found", "The requested local route was not found.", 404
        )

    def _post_operation(
        self, path: str, body: dict[str, Any]
    ) -> tuple[str, Callable[[], dict[str, Any]]]:
        service = self.server.service
        if path == "/api/investigations":
            _exact_keys(body, {"request_id", "incident_id"})
            incident_id = _identifier(body["incident_id"], "incident_id")
            return "investigation", lambda: service.start_investigation(incident_id)
        if path == "/api/actions/propose":
            _exact_keys(body, {"request_id", "investigation_id"})
            investigation_id = _uuid(body["investigation_id"], "investigation_id")
            return "proposal", lambda: service.propose(investigation_id)
        if path == "/api/actions/decision":
            _exact_keys(body, {"request_id", "run_id", "decision", "confirmation"})
            run_id = _uuid(body["run_id"], "run_id")
            decision = body["decision"]
            if decision not in {"approve", "reject"}:
                raise WebConsoleError(
                    "invalid_request", "Decision must be approve or reject."
                )
            confirmation = _confirmation(body["confirmation"])
            return "plan_decision", lambda: service.decide(
                run_id, approve=decision == "approve", confirmation=confirmation
            )
        if path == "/api/actions/admit":
            _exact_keys(body, {"request_id", "run_id"})
            run_id = _uuid(body["run_id"], "run_id")
            return "admission", lambda: service.admit(run_id)
        if path == "/api/actions/authorize":
            _exact_keys(body, {"request_id", "action_id", "confirmation"})
            action_id = _uuid(body["action_id"], "action_id")
            confirmation = _confirmation(body["confirmation"])
            return "authorization", lambda: service.authorize(action_id, confirmation)
        if path == "/api/actions/execute":
            _exact_keys(body, {"request_id", "action_id", "confirmation"})
            action_id = _uuid(body["action_id"], "action_id")
            confirmation = _confirmation(body["confirmation"])
            return "execution", lambda: service.execute(action_id, confirmation)
        raise WebConsoleError(
            "not_found", "The requested local route was not found.", 404
        )


def _single_query(query: dict[str, list[str]], name: str) -> str:
    if set(query) != {name} or len(query[name]) != 1:
        raise WebConsoleError(
            "invalid_request", f"Exactly one {name} value is required."
        )
    return query[name][0]


def _exact_keys(body: dict[str, Any], expected: set[str]) -> None:
    if set(body) != expected:
        raise WebConsoleError("invalid_request", "The request fields are invalid.")


def _confirmation(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise WebConsoleError(
            "invalid_request", "An exact human confirmation is required."
        )
    return value


def _public_failed_investigation(
    result: dict[str, Any], incident_id: str
) -> dict[str, Any]:
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    return {
        "status": result.get("status", "error"),
        "incident_id": incident_id,
        "model": result.get("model"),
        "error": {
            "code": str(error.get("code", "investigation_incomplete")),
            "message": str(error.get("message", "The investigation did not complete."))[
                :500
            ],
        },
        "task_completed": False,
        "evidence_complete": result.get("evidence_complete") is True,
        "causality": result.get("causality", {"status": "unproven"}),
    }


def _public_investigation(
    result: dict[str, Any],
    investigation_id: str,
    snapshot: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    observation = result["observations"][0]
    trace = result["tool_trace"][0]
    return {
        "status": "complete",
        "investigation_id": investigation_id,
        "incident_id": snapshot["incident_id"],
        "model": result["model"],
        "summary": result["answer"],
        "answer_source": "deterministic",
        "task_completed": True,
        "evidence_complete": True,
        "evidence_digest": snapshot["evidence_digest"],
        "observed_at": snapshot["observed_at"],
        "tool_trace": {
            "tool": trace["tool"],
            "status": trace["status"],
            "arguments": trace["arguments"],
            "evidence_refs": trace["evidence_refs"],
        },
        "evidence_refs": result["evidence_refs"],
        "incident": evidence["incident"],
        "potentially_impacted_services": evidence["potentially_impacted_services"],
        "related_deployments": evidence["related_deployments"],
        "related_alerts": evidence["related_alerts"],
        "causality": result["causality"],
        "uncertainty": result["uncertainty"],
        "raw_model_prose_used": False,
        "observed_evidence": observation["evidence"],
    }


def create_server(
    home: str | Path,
    port: int = 8765,
    *,
    store_factory: Callable[[], FalkorStore] | None = None,
    provider_factory: Callable[[], Any] | None = None,
) -> _ConsoleHTTPServer:
    if type(port) is not int or not 0 <= port <= 65_535:
        raise WebConsoleError(
            "invalid_port", "The port must be 0 or between 1 and 65535."
        )
    workspace = ActionWorkspace.load(home)
    home_lock = _ActionHomeLock(workspace.home)
    home_lock.acquire()
    service: WebConsoleService | None = None
    try:
        service = WebConsoleService(
            workspace.home,
            store_factory=store_factory,
            provider_factory=provider_factory,
        )
        return _ConsoleHTTPServer(port, service, home_lock)
    except Exception:
        if service is not None:
            service.close()
        home_lock.release()
        raise


def serve(home: str | Path, port: int = 8765) -> None:
    server = create_server(home, port)
    print(
        f"IncidentGraph Agent is listening at http://127.0.0.1:{server.server_address[1]}/"
    )
    print("This local Console uses the initialized Action home; press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("Stopping the local IncidentGraph Agent Web Console.")
    finally:
        server.server_close()
