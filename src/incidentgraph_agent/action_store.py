from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agentic_intent_gate.runtime import ToolBinding

from incidentgraph_agent.action_evidence import (
    EvidenceSnapshotError,
    canonical_json,
    validate_snapshot,
)
from incidentgraph_agent.evidence import EvidenceValidationError, parse_timestamp
from incidentgraph_agent.errors import InvalidInput
from incidentgraph_agent.validation import validate_identifier


class StoreOperationError(RuntimeError):
    """A local action store could not prove its durable state."""


class StoreIdentityMismatch(StoreOperationError):
    pass


class EffectPayloadConflict(StoreOperationError):
    pass


class StoreOutcomeUnknown(StoreOperationError):
    pass


@dataclass(frozen=True, slots=True)
class TicketReceipt:
    ticket_id: str
    receipt_id: str
    incident_id: str
    evidence_digest: str
    payload_digest: str
    effect_key: str
    execution_scope: str
    target: str
    tool_name: str
    tool_version: str
    adapter_id: str
    binding_revision: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ticket_id": self.ticket_id,
            "receipt_id": self.receipt_id,
            "incident_id": self.incident_id,
            "evidence_digest": self.evidence_digest,
            "payload_digest": self.payload_digest,
            "effect_key": self.effect_key,
            "execution_scope": self.execution_scope,
            "target": self.target,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "adapter_id": self.adapter_id,
            "binding_revision": self.binding_revision,
            "created_at": self.created_at,
        }


def _create_private_sqlite_file(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise StoreOperationError(
            "Action storage already exists; refusing to overwrite it."
        )
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:
        raise StoreOperationError(
            "Could not provision private action storage."
        ) from exc


def _connect_rw(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise StoreOperationError("Required action storage is missing or unsafe.")
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=rw",
            uri=True,
            timeout=1,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout=1000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection
    except sqlite3.Error as exc:
        raise StoreOperationError("The local action store is unavailable.") from exc


def _binding_document(binding: ToolBinding) -> str:
    return canonical_json(asdict(binding))


def _check_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


class EvidenceSnapshotStore:
    """Consumer-owned SQLite store for immutable evidence and Run references."""

    def __init__(self, path: Path, store_id: str) -> None:
        self.path = Path(path).resolve()
        self.store_id = store_id
        if not _check_uuid(store_id):
            raise StoreIdentityMismatch("Evidence snapshot store identity is invalid.")
        self._check_store()

    @classmethod
    def provision(cls, path: Path, store_id: str) -> EvidenceSnapshotStore:
        path = Path(path).resolve()
        if not path.parent.is_dir():
            raise StoreOperationError("The action home must be provisioned first.")
        _create_private_sqlite_file(path)
        try:
            with closing(_connect_rw(path)) as db:
                db.executescript(
                    """
                    CREATE TABLE store_metadata (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                        store_id TEXT NOT NULL
                    ) STRICT;
                    CREATE TABLE snapshots (
                        evidence_digest TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        document TEXT NOT NULL,
                        PRIMARY KEY (evidence_digest, observed_at)
                    ) STRICT;
                    CREATE TABLE run_snapshots (
                        run_id TEXT PRIMARY KEY NOT NULL,
                        evidence_digest TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        FOREIGN KEY (evidence_digest, observed_at)
                            REFERENCES snapshots (evidence_digest, observed_at)
                    ) STRICT;
                    """
                )
                db.execute("INSERT INTO store_metadata VALUES (1, 1, ?)", (store_id,))
                db.execute("PRAGMA user_version=1")
                db.commit()
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not initialize the evidence snapshot store."
            ) from exc
        return cls(path, store_id)

    def put(self, snapshot: dict[str, Any]) -> None:
        try:
            validate_snapshot(snapshot)
        except EvidenceSnapshotError as exc:
            raise StoreOperationError(
                "The evidence snapshot failed integrity validation."
            ) from exc
        document = canonical_json(snapshot)
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO snapshots(evidence_digest, observed_at, document) VALUES (?,?,?)",
                (snapshot["evidence_digest"], snapshot["observed_at"], document),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise StoreOperationError(
                "This immutable evidence snapshot already exists."
            ) from exc
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise StoreOperationError(
                "Could not persist the evidence snapshot."
            ) from exc
        finally:
            connection.close()
        stored = self.get(snapshot["evidence_digest"], snapshot["observed_at"])
        if stored != snapshot:
            raise StoreOutcomeUnknown(
                "The persisted evidence snapshot could not be verified."
            )

    def link_run(self, run_id: str, snapshot: dict[str, Any]) -> None:
        if not _check_uuid(run_id):
            raise StoreOperationError("The Runtime Run identity is invalid.")
        self.get(snapshot["evidence_digest"], snapshot["observed_at"])
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO run_snapshots(run_id, evidence_digest, observed_at) VALUES (?,?,?)",
                (run_id, snapshot["evidence_digest"], snapshot["observed_at"]),
            )
            connection.commit()
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise StoreOperationError(
                "Could not link the Run to its evidence snapshot."
            ) from exc
        finally:
            connection.close()
        linked = self.for_run(run_id)
        if linked != snapshot:
            raise StoreOutcomeUnknown("The Run-to-evidence link could not be verified.")

    def for_run(self, run_id: str) -> dict[str, Any] | None:
        if not _check_uuid(run_id):
            raise StoreOperationError("The Runtime Run identity is invalid.")
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            row = connection.execute(
                """SELECT s.document FROM run_snapshots AS r
                   JOIN snapshots AS s USING (evidence_digest, observed_at)
                   WHERE r.run_id=?""",
                (run_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not read the Run evidence snapshot."
            ) from exc
        finally:
            connection.close()
        if row is None:
            return None
        return self._decode(row[0])

    def get(self, evidence_digest: str, observed_at: str) -> dict[str, Any] | None:
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            row = connection.execute(
                "SELECT document FROM snapshots WHERE evidence_digest=? AND observed_at=?",
                (evidence_digest, observed_at),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreOperationError("Could not read the evidence snapshot.") from exc
        finally:
            connection.close()
        return None if row is None else self._decode(row[0])

    def _decode(self, raw: object) -> dict[str, Any]:
        try:
            if not isinstance(raw, str):
                raise ValueError
            snapshot = json.loads(raw)
            validate_snapshot(snapshot)
            if canonical_json(snapshot) != raw:
                raise ValueError
            return snapshot
        except (
            ValueError,
            TypeError,
            json.JSONDecodeError,
            EvidenceSnapshotError,
        ) as exc:
            raise StoreOutcomeUnknown(
                "Stored evidence failed integrity validation."
            ) from exc

    def _check_store(self) -> None:
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise StoreOutcomeUnknown(
                    "The evidence snapshot store failed integrity checks."
                )
            if connection.execute("PRAGMA user_version").fetchone() != (1,):
                raise StoreIdentityMismatch(
                    "Evidence snapshot store version is unsupported."
                )
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not validate the evidence snapshot store."
            ) from exc
        finally:
            connection.close()

    def _check_identity(self, connection: sqlite3.Connection) -> None:
        try:
            rows = connection.execute(
                "SELECT schema_version, store_id FROM store_metadata WHERE singleton=1"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StoreIdentityMismatch(
                "Evidence snapshot store identity is unreadable."
            ) from exc
        if rows != [(1, self.store_id)]:
            raise StoreIdentityMismatch(
                "Evidence snapshot store identity does not match."
            )


class LocalIncidentTicketStore:
    """SQLite effects and receipts, separate from both Runtime and evidence data."""

    _TICKET_COLUMNS = (
        "ticket_id, receipt_id, incident_id, evidence_digest, payload_digest, effect_key, "
        "execution_scope, target, tool_name, tool_version, adapter_id, binding_revision, created_at"
    )

    def __init__(self, path: Path, store_id: str, binding: ToolBinding) -> None:
        self.path = Path(path).resolve()
        self.store_id = store_id
        self.binding = binding
        if not _check_uuid(store_id):
            raise StoreIdentityMismatch("Ticket store identity is invalid.")
        self._check_store()

    @classmethod
    def provision(
        cls, path: Path, store_id: str, binding: ToolBinding
    ) -> LocalIncidentTicketStore:
        path = Path(path).resolve()
        if not path.parent.is_dir():
            raise StoreOperationError("The action home must be provisioned first.")
        _create_private_sqlite_file(path)
        try:
            with closing(_connect_rw(path)) as db:
                db.executescript(
                    """
                    CREATE TABLE store_metadata (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                        store_id TEXT NOT NULL,
                        binding_json TEXT NOT NULL
                    ) STRICT;
                    CREATE TABLE tickets (
                        ticket_id TEXT PRIMARY KEY NOT NULL,
                        receipt_id TEXT UNIQUE NOT NULL,
                        incident_id TEXT NOT NULL,
                        evidence_digest TEXT NOT NULL,
                        payload_digest TEXT NOT NULL,
                        effect_key TEXT UNIQUE NOT NULL,
                        execution_scope TEXT NOT NULL,
                        target TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        tool_version TEXT NOT NULL,
                        adapter_id TEXT NOT NULL,
                        binding_revision TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(effect_key, payload_digest)
                    ) STRICT;
                    CREATE INDEX tickets_by_incident ON tickets(incident_id, created_at);
                    """
                )
                db.execute(
                    "INSERT INTO store_metadata VALUES (1, 1, ?, ?)",
                    (store_id, _binding_document(binding)),
                )
                db.execute("PRAGMA user_version=1")
                db.commit()
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not initialize the local ticket store."
            ) from exc
        return cls(path, store_id, binding)

    def find_effect(self, effect_key: str, payload_digest: str) -> TicketReceipt | None:
        if not _check_uuid(effect_key) or not _is_digest(payload_digest):
            raise StoreOperationError("The Runtime effect identity is invalid.")
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            row = connection.execute(
                f"SELECT {self._TICKET_COLUMNS} FROM tickets WHERE effect_key=?",
                (effect_key,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not read the local ticket effect store."
            ) from exc
        finally:
            connection.close()
        if row is None:
            return None
        record = self._record(row)
        if record.payload_digest != payload_digest:
            raise EffectPayloadConflict(
                "The Runtime effect key is bound to another payload."
            )
        self._verify_binding_record(record)
        return record

    def create_or_get(
        self,
        *,
        incident_id: str,
        evidence_digest: str,
        payload_digest: str,
        effect_key: str,
    ) -> TicketReceipt:
        try:
            validate_identifier(incident_id, field="incident ID")
        except Exception as exc:
            raise StoreOperationError(
                "The ticket incident identity is invalid."
            ) from exc
        if (
            not _check_uuid(effect_key)
            or not _is_digest(evidence_digest)
            or not _is_digest(payload_digest)
        ):
            raise StoreOperationError("The ticket effect identity is invalid.")
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {self._TICKET_COLUMNS} FROM tickets WHERE effect_key=?",
                (effect_key,),
            ).fetchone()
            if row is not None:
                record = self._record(row)
                if record.payload_digest != payload_digest:
                    raise EffectPayloadConflict(
                        "The Runtime effect key is bound to another payload."
                    )
                self._verify_binding_record(record)
                connection.commit()
                return record

            ticket_id = str(uuid4())
            receipt_id = str(uuid4())
            created_at = _now_text()
            binding = self.binding
            connection.execute(
                f"INSERT INTO tickets({self._TICKET_COLUMNS}) VALUES ({','.join('?' for _ in range(13))})",
                (
                    ticket_id,
                    receipt_id,
                    incident_id,
                    evidence_digest,
                    payload_digest,
                    effect_key,
                    binding.execution_scope,
                    binding.target,
                    binding.tool_name,
                    binding.tool_version,
                    binding.adapter_id,
                    binding.binding_revision,
                    created_at,
                ),
            )
            connection.commit()
        except (EffectPayloadConflict, StoreIdentityMismatch, StoreOutcomeUnknown):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            # Even a commit error can arrive after SQLite made the row durable.
            raise StoreOutcomeUnknown(
                "The local ticket commit outcome is uncertain."
            ) from exc
        finally:
            connection.close()

        try:
            verified = self.find_effect(effect_key, payload_digest)
        except Exception as exc:
            # The INSERT is already committed. Any failed readback is therefore
            # uncertainty about a durable effect, regardless of the exception type.
            raise StoreOutcomeUnknown(
                "The ticket committed but its receipt could not be verified."
            ) from exc
        if (
            verified is None
            or verified.ticket_id != ticket_id
            or verified.receipt_id != receipt_id
        ):
            raise StoreOutcomeUnknown("The ticket commit could not be verified.")
        return verified

    def get_receipt(self, receipt_id: str) -> TicketReceipt | None:
        if not _check_uuid(receipt_id):
            raise StoreOperationError("The ticket receipt identity is invalid.")
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            row = connection.execute(
                f"SELECT {self._TICKET_COLUMNS} FROM tickets WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not read the local ticket receipt."
            ) from exc
        finally:
            connection.close()
        if row is None:
            return None
        record = self._record(row)
        self._verify_binding_record(record)
        return record

    def list_tickets(self) -> list[dict[str, str]]:
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            rows = connection.execute(
                f"SELECT {self._TICKET_COLUMNS} FROM tickets ORDER BY created_at, ticket_id LIMIT 501"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StoreOperationError("Could not list local incident tickets.") from exc
        finally:
            connection.close()
        if len(rows) > 500:
            raise StoreOperationError("The local ticket list exceeded its read limit.")
        return [self._record(row).to_dict() for row in rows]

    def count(self) -> int:
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            result = connection.execute("SELECT count(*) FROM tickets").fetchone()
            return int(result[0])
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not count local incident tickets."
            ) from exc
        finally:
            connection.close()

    def _check_store(self) -> None:
        connection = _connect_rw(self.path)
        try:
            self._check_identity(connection)
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise StoreOutcomeUnknown(
                    "The local ticket store failed integrity checks."
                )
            if connection.execute("PRAGMA user_version").fetchone() != (1,):
                raise StoreIdentityMismatch(
                    "Local ticket store version is unsupported."
                )
        except sqlite3.Error as exc:
            raise StoreOperationError(
                "Could not validate the local ticket store."
            ) from exc
        finally:
            connection.close()

    def _check_identity(self, connection: sqlite3.Connection) -> None:
        try:
            rows = connection.execute(
                "SELECT schema_version, store_id, binding_json FROM store_metadata WHERE singleton=1"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StoreIdentityMismatch(
                "Local ticket store identity is unreadable."
            ) from exc
        expected = (1, self.store_id, _binding_document(self.binding))
        if rows != [expected]:
            raise StoreIdentityMismatch("Local ticket store identity does not match.")

    def _record(self, row: object) -> TicketReceipt:
        if not isinstance(row, (tuple, list)) or len(row) != 13:
            raise StoreOutcomeUnknown("The stored ticket receipt has an invalid shape.")
        record = TicketReceipt(*row)
        if (
            not _check_uuid(record.ticket_id)
            or not _check_uuid(record.receipt_id)
            or not _check_uuid(record.effect_key)
            or not _is_digest(record.evidence_digest)
            or not _is_digest(record.payload_digest)
            or not isinstance(record.incident_id, str)
            or not record.incident_id
            or not isinstance(record.created_at, str)
            or not record.created_at
        ):
            raise StoreOutcomeUnknown("The stored ticket receipt failed validation.")
        try:
            validate_identifier(record.incident_id, field="incident ID")
            parsed_created_at = parse_timestamp(
                record.created_at, field="ticket creation time"
            )
        except (EvidenceValidationError, InvalidInput, TypeError, ValueError) as exc:
            raise StoreOutcomeUnknown(
                "The stored ticket receipt failed validation."
            ) from exc
        expected_created_at = (
            parsed_created_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        if record.created_at != expected_created_at:
            raise StoreOutcomeUnknown(
                "The stored ticket timestamp is not canonical UTC."
            )
        return record

    def _verify_binding_record(self, record: TicketReceipt) -> None:
        expected = self.binding
        if (
            record.execution_scope,
            record.target,
            record.tool_name,
            record.tool_version,
            record.adapter_id,
            record.binding_revision,
        ) != (
            expected.execution_scope,
            expected.target,
            expected.tool_name,
            expected.tool_version,
            expected.adapter_id,
            expected.binding_revision,
        ):
            raise StoreIdentityMismatch(
                "Stored ticket authority does not match this Runtime."
            )


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _now_text() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
