from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from agentic_intent_gate.runtime import (
    EXTERNAL_PAYLOAD_REVISION,
    ActionCommand,
    AdmissionCommand,
    ApprovalChoice,
    ApprovalDecisionRequest,
    BoundCapabilityAuthorization,
    CapabilityVerifier,
    ExecutableRegistry,
    ExecutionSpec,
    Intent,
    Outcome,
    ToolBinding,
    ToolCapability,
    ToolOutcome,
    open_runtime,
    provision_capability,
)

from incidentgraph_agent.action_evidence import (
    EvidenceSnapshotError,
    MAX_SNAPSHOT_AGE_SECONDS,
    canonical_json,
    ensure_snapshot_fresh,
    snapshot_from_graph_evidence,
    snapshot_matches_current,
    source_data_ages,
    utc_now_text,
    validate_snapshot,
)
from incidentgraph_agent.action_store import (
    EffectPayloadConflict,
    EvidenceSnapshotStore,
    LocalIncidentTicketStore,
    StoreIdentityMismatch,
    StoreOperationError,
    StoreOutcomeUnknown,
    TicketReceipt,
)
from incidentgraph_agent.errors import IncidentGraphError
from incidentgraph_agent.repository import IncidentRepository
from incidentgraph_agent.validation import validate_identifier

TOOL_NAME = "create_local_incident_ticket"
TOOL_VERSION = "1"
ADAPTER_ID = "incidentgraph-local-ticket"
BINDING_REVISION = "incident-ticket-v1"
SUBJECT_ID = "incidentgraph-local-operator"
POLICY_REVISION = "incidentgraph-local-policy-v1"
TARGET_PREFIX = "incidentgraph-ticket-store-"
SNAPSHOT_FILE = "snapshots.sqlite3"
TICKET_FILE = "tickets.sqlite3"
GATE_FILE = "gate.sqlite3"
IDENTITY_FILE = "identity.json"
CAPABILITY_FILE = "capability"
_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z"
)
_OPERATIONS = ("read", "admit", "authorize", "execute")
ARGUMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "incident_id",
        "evidence_digest",
        "snapshot_observed_at",
        "affected_service_count",
    ],
    "properties": {
        "incident_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        },
        "evidence_digest": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": r"^[0-9a-f]{64}$",
        },
        "snapshot_observed_at": {
            "type": "string",
            "minLength": 27,
            "maxLength": 27,
            "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$",
        },
        "affected_service_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
        },
    },
}


class ActionWorkflowError(IncidentGraphError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ActionIdentityError(ActionWorkflowError):
    pass


@dataclass(frozen=True, slots=True)
class ActionWorkspace:
    home: Path
    binding: ToolBinding
    ticket_store_id: str
    snapshot_store_id: str

    @property
    def gate_path(self) -> Path:
        return self.home / GATE_FILE

    @property
    def ticket_path(self) -> Path:
        return self.home / TICKET_FILE

    @property
    def snapshot_path(self) -> Path:
        return self.home / SNAPSHOT_FILE

    @property
    def identity_path(self) -> Path:
        return self.home / IDENTITY_FILE

    @property
    def capability_path(self) -> Path:
        return self.home / CAPABILITY_FILE

    @classmethod
    def load(
        cls, home: str | Path, *, allow_missing_gate: bool = False
    ) -> ActionWorkspace:
        candidate = Path(home).expanduser()
        if candidate.is_symlink():
            raise ActionIdentityError(
                "invalid_action_home", "The action home must not be a symbolic link."
            )
        root = candidate.resolve()
        if not root.is_dir() or root.is_symlink():
            raise ActionIdentityError(
                "action_not_initialized", "Run `incidentgraph action init` first."
            )
        identity_path = root / IDENTITY_FILE
        capability_path = root / CAPABILITY_FILE
        if identity_path.is_symlink() or capability_path.is_symlink():
            raise ActionIdentityError(
                "invalid_action_identity", "Action identity files are unsafe."
            )
        try:
            raw = identity_path.read_bytes()
            if len(raw) > 4096:
                raise ValueError("oversized")
            identity = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ActionIdentityError(
                "invalid_action_identity", "Action identity is missing or malformed."
            ) from exc
        if (
            type(identity) is not dict
            or set(identity)
            != {
                "schema_version",
                "binding",
                "ticket_store_id",
                "snapshot_store_id",
            }
            or type(identity.get("schema_version")) is not int
            or identity["schema_version"] != 1
        ):
            raise ActionIdentityError(
                "invalid_action_identity", "Action identity schema is unsupported."
            )
        values = identity.get("binding")
        if type(values) is not dict or set(values) != {
            "tool_name",
            "tool_version",
            "adapter_id",
            "binding_revision",
            "target",
            "execution_scope",
        }:
            raise ActionIdentityError(
                "invalid_action_identity", "Action binding identity is malformed."
            )
        try:
            binding = ToolBinding(**values)
        except Exception as exc:
            raise ActionIdentityError(
                "invalid_action_identity", "Action binding identity is invalid."
            ) from exc
        if (
            (
                binding.tool_name,
                binding.tool_version,
                binding.adapter_id,
                binding.binding_revision,
            )
            != (TOOL_NAME, TOOL_VERSION, ADAPTER_ID, BINDING_REVISION)
            or not binding.target.startswith(TARGET_PREFIX)
            or not _UUID.fullmatch(binding.execution_scope)
            or not _UUID.fullmatch(binding.target.removeprefix(TARGET_PREFIX))
        ):
            raise ActionIdentityError(
                "invalid_action_identity",
                "Action binding does not match the reviewed profile.",
            )
        ticket_store_id = identity.get("ticket_store_id")
        snapshot_store_id = identity.get("snapshot_store_id")
        if (
            not isinstance(ticket_store_id, str)
            or not _UUID.fullmatch(ticket_store_id)
            or not isinstance(snapshot_store_id, str)
            or not _UUID.fullmatch(snapshot_store_id)
            or ticket_store_id == snapshot_store_id
        ):
            raise ActionIdentityError(
                "invalid_action_identity", "Action store identities are invalid."
            )
        workspace = cls(root, binding, ticket_store_id, snapshot_store_id)
        required = (workspace.gate_path, workspace.ticket_path, workspace.snapshot_path)
        if len({path.resolve() for path in required}) != len(required):
            raise ActionIdentityError(
                "invalid_action_home",
                "Runtime, ticket, and snapshot stores must be separate.",
            )
        for path in required:
            if path.is_symlink():
                raise ActionIdentityError(
                    "invalid_action_home",
                    "Action database paths must not be symbolic links.",
                )
            if not path.is_file() and not (
                allow_missing_gate and path == workspace.gate_path
            ):
                raise ActionIdentityError(
                    "action_storage_missing",
                    "An initialized action database is missing; refusing to recreate or reset it.",
                )
        if not capability_path.is_file() or not _TOKEN.fullmatch(
            capability_path.read_text("ascii").strip()
        ):
            raise ActionIdentityError(
                "invalid_action_capability",
                "The local operator capability is missing or invalid.",
            )
        return workspace


def default_action_home(override: str | Path | None = None) -> Path:
    configured = override or os.environ.get("INCIDENTGRAPH_ACTION_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise ActionWorkflowError(
            "action_home_unconfigured",
            "Set INCIDENTGRAPH_ACTION_HOME or LOCALAPPDATA before initializing local action storage.",
        )
    return (Path(local_app_data) / "IncidentGraphAgent" / "phase3-actions").resolve()


class UnusedPlanProvider:
    async def generate_plan(
        self, intent, *, tools, plan_schema, timeout_seconds
    ) -> str:
        raise RuntimeError(
            "Action planning is only enabled by the explicit proposal command."
        )


class DeterministicTicketPlanProvider:
    """Consumer-owned deterministic provider; it never uses model-generated arguments."""

    def __init__(self, binding: ToolBinding, snapshot: dict[str, Any]):
        self.binding = binding
        self.snapshot = validate_snapshot(snapshot)
        self.arguments = action_arguments(snapshot)
        self.expected_intent = action_intent(snapshot)

    async def generate_plan(
        self, intent, *, tools, plan_schema, timeout_seconds
    ) -> str:
        if intent.text != self.expected_intent:
            return "{}"
        return canonical_json(
            {
                "goal": f"Create a local incident ticket for {self.snapshot['incident_id']} from reviewed graph evidence.",
                "target": self.binding.target,
                "steps": [
                    {
                        "tool_name": TOOL_NAME,
                        "tool_version": TOOL_VERSION,
                        "arguments": self.arguments,
                    }
                ],
                "risk_class": "SIDE_EFFECT",
            }
        )


class LocalIncidentTicketExecutor:
    """One fixed SIDE_EFFECT adapter; it can only persist a local incident ticket."""

    def __init__(
        self,
        binding: ToolBinding,
        snapshots: EvidenceSnapshotStore,
        tickets: LocalIncidentTicketStore,
        repository: IncidentRepository | None,
    ) -> None:
        self.binding = binding
        self.snapshots = snapshots
        self.tickets = tickets
        self.repository = repository

    async def execute(self, spec, effect_key, context) -> ToolOutcome:
        payload_digest = _payload_digest(spec)

        def outcome(category, *, receipt=None, reason=None) -> ToolOutcome:
            return ToolOutcome(
                category,
                effect_key,
                spec.execution_scope,
                spec.target,
                payload_digest,
                receipt,
                reason,
            )

        if (
            not self.binding.matches(spec)
            or spec.identity_revision != EXTERNAL_PAYLOAD_REVISION
        ):
            return outcome(Outcome.CONFIRMED_FAILURE, reason="STORE_IDENTITY_MISMATCH")
        arguments = dict(spec.arguments)
        if not _valid_arguments(arguments):
            return outcome(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
        try:
            if not _UUID.fullmatch(effect_key) or not _UUID.fullmatch(
                context.attempt_id
            ):
                return outcome(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
            # An already committed identical effect returns its receipt without dispatch.
            existing = self.tickets.find_effect(effect_key, payload_digest)
            if existing is not None:
                if (
                    existing.incident_id != arguments["incident_id"]
                    or existing.evidence_digest != arguments["evidence_digest"]
                ):
                    return outcome(Outcome.UNKNOWN_OUTCOME)
                return outcome(Outcome.SUCCESS, receipt=existing.receipt_id)
        except EffectPayloadConflict:
            return outcome(Outcome.CONFIRMED_FAILURE, reason="PAYLOAD_CONFLICT")
        except StoreIdentityMismatch:
            return outcome(Outcome.CONFIRMED_FAILURE, reason="STORE_IDENTITY_MISMATCH")
        except (StoreOperationError, StoreOutcomeUnknown):
            return outcome(Outcome.UNKNOWN_OUTCOME)

        try:
            snapshot = self.snapshots.get(
                arguments["evidence_digest"], arguments["snapshot_observed_at"]
            )
            if snapshot is None or not _arguments_match_snapshot(arguments, snapshot):
                return outcome(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
            ensure_snapshot_fresh(snapshot)
            if context.expired() or self.repository is None:
                return outcome(Outcome.UNKNOWN_OUTCOME)
            current_evidence = self.repository.investigate(arguments["incident_id"])
            current = snapshot_from_graph_evidence(
                current_evidence,
                incident_id=arguments["incident_id"],
                graph_identity=self.repository.store.settings.graph_name,
            )
            if not snapshot_matches_current(snapshot, current):
                return outcome(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
            if context.expired():
                return outcome(Outcome.UNKNOWN_OUTCOME)
        except (EvidenceSnapshotError, IncidentGraphError, StoreIdentityMismatch):
            # These checks finish before create_or_get can attempt a ticket effect.
            return outcome(Outcome.CONFIRMED_FAILURE, reason="INPUT_REJECTED")
        except EffectPayloadConflict:
            return outcome(Outcome.CONFIRMED_FAILURE, reason="PAYLOAD_CONFLICT")
        except (StoreOperationError, StoreOutcomeUnknown):
            return outcome(Outcome.UNKNOWN_OUTCOME)
        except Exception:
            return outcome(Outcome.UNKNOWN_OUTCOME)

        # From this call onward a ticket may have committed. The store converts
        # post-commit readback failures to StoreOutcomeUnknown; only the store's
        # explicit pre-commit identity/payload rejections remain confirmed failures.
        try:
            receipt = self.tickets.create_or_get(
                incident_id=arguments["incident_id"],
                evidence_digest=arguments["evidence_digest"],
                payload_digest=payload_digest,
                effect_key=effect_key,
            )
            if context.expired():
                return outcome(Outcome.UNKNOWN_OUTCOME)
            return outcome(Outcome.SUCCESS, receipt=receipt.receipt_id)
        except StoreIdentityMismatch:
            return outcome(Outcome.CONFIRMED_FAILURE, reason="STORE_IDENTITY_MISMATCH")
        except EffectPayloadConflict:
            return outcome(Outcome.CONFIRMED_FAILURE, reason="PAYLOAD_CONFLICT")
        except (StoreOperationError, StoreOutcomeUnknown):
            return outcome(Outcome.UNKNOWN_OUTCOME)
        except Exception:
            return outcome(Outcome.UNKNOWN_OUTCOME)


@contextmanager
def open_action_runtime(
    home: str | Path,
    *,
    graph_store=None,
    plan_provider=None,
    initialize_gate: bool = False,
) -> Iterator[tuple[Any, Any, EvidenceSnapshotStore, LocalIncidentTicketStore]]:
    workspace = ActionWorkspace.load(home, allow_missing_gate=initialize_gate)
    snapshots = EvidenceSnapshotStore(
        workspace.snapshot_path, workspace.snapshot_store_id
    )
    tickets = LocalIncidentTicketStore(
        workspace.ticket_path, workspace.ticket_store_id, workspace.binding
    )
    repository = IncidentRepository(graph_store) if graph_store is not None else None
    executor = LocalIncidentTicketExecutor(
        workspace.binding, snapshots, tickets, repository
    )
    token = workspace.capability_path.read_text("ascii").strip()
    verifier = CapabilityVerifier(
        token,
        subject_id=SUBJECT_ID,
        binding=workspace.binding,
        operations=_OPERATIONS,
    )
    capability = ToolCapability(
        workspace.binding,
        "Create one durable local ticket for a complete incident investigation.",
        ARGUMENT_SCHEMA,
        semantic_rule=_argument_semantics,
    )
    registry = ExecutableRegistry(executor, binding=workspace.binding)
    policy = BoundCapabilityAuthorization(verifier, policy_revision=POLICY_REVISION)
    with open_runtime(
        gate_path=workspace.gate_path,
        provider=plan_provider or UnusedPlanProvider(),
        capability=capability,
        registry=registry,
        verifier=verifier,
        policy=policy,
        predecessor_stopped=True,
        execution_timeout_seconds=5,
    ) as runtime:
        principal = verifier.verify("Bearer " + token)
        yield runtime, principal, snapshots, tickets


def provision_action_workspace(home: str | Path) -> dict[str, Any]:
    root = Path(home).expanduser()
    if root.exists() or root.is_symlink():
        raise ActionWorkflowError(
            "action_home_exists",
            "Action initialization requires a new, unused directory.",
        )
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        root = root.resolve()
        if os.name != "nt":
            os.chmod(root, 0o700)
    except OSError as exc:
        raise ActionWorkflowError(
            "action_home_unavailable", "Could not create the action home."
        ) from exc

    scope = str(uuid4())
    target_id = TARGET_PREFIX + str(uuid4())
    binding = ToolBinding(
        TOOL_NAME,
        TOOL_VERSION,
        ADAPTER_ID,
        BINDING_REVISION,
        target_id,
        scope,
    )
    ticket_store_id = str(uuid4())
    snapshot_store_id = str(uuid4())
    identity = {
        "schema_version": 1,
        "binding": asdict(binding),
        "ticket_store_id": ticket_store_id,
        "snapshot_store_id": snapshot_store_id,
    }
    workspace = ActionWorkspace(root, binding, ticket_store_id, snapshot_store_id)
    try:
        _write_private_new_file(
            workspace.identity_path, canonical_json(identity).encode("utf-8")
        )
        provision_capability(workspace.capability_path)
        EvidenceSnapshotStore.provision(workspace.snapshot_path, snapshot_store_id)
        LocalIncidentTicketStore.provision(
            workspace.ticket_path, ticket_store_id, binding
        )
        with open_action_runtime(root, initialize_gate=True) as _session:
            # Opening the pinned Runtime explicitly provisions only its private gate ledger.
            pass
    except Exception as exc:
        if isinstance(exc, ActionWorkflowError):
            raise
        raise ActionWorkflowError(
            "action_initialization_failed",
            "Action storage initialization failed; preserve the partially created files for inspection.",
        ) from exc
    return {
        "status": "initialized",
        "action_home": str(root),
        "target": binding.target,
        "execution_scope": binding.execution_scope,
        "runtime_store": GATE_FILE,
        "ticket_store": TICKET_FILE,
        "evidence_store": SNAPSHOT_FILE,
        "capability": "provisioned; material withheld",
        "execution_budget_seconds": 5,
    }


def create_action_proposal(
    home: str | Path,
    snapshot: dict[str, Any],
    *,
    graph_store,
) -> dict[str, Any]:
    workspace = ActionWorkspace.load(home)
    snapshot = validate_snapshot(snapshot)
    try:
        with open_action_runtime(
            home,
            graph_store=graph_store,
            plan_provider=DeterministicTicketPlanProvider(workspace.binding, snapshot),
        ) as (runtime, principal, snapshots, _tickets):
            runtime.gateway.require(principal, "read")
            _validate_snapshot_current(snapshot, runtime, graph_store)
            snapshots.put(snapshot)
            intent = action_intent(snapshot)
            run = asyncio.run(runtime.run_service.create(Intent(intent)))
            if run.state.value != "AwaitingApproval" or run.plan is None:
                raise ActionWorkflowError(
                    "plan_rejected",
                    "The deterministic Action Plan did not pass Runtime validation.",
                )
            _validate_run_plan(run, snapshot, workspace.binding)
            snapshots.link_run(run.run_id, snapshot)
            return {
                "status": "awaiting_approval",
                "run": run.to_dict(),
                "plan_digest": plan_digest(run),
                "evidence_digest": snapshot["evidence_digest"],
                "snapshot_observed_at": snapshot["observed_at"],
                "ticket_count": 0,
            }
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def review_run(home: str | Path, run_id: str) -> dict[str, Any]:
    try:
        with open_action_runtime(home) as (runtime, principal, snapshots, _tickets):
            runtime.gateway.require(principal, "read")
            run = runtime.run_service.get(run_id)
            snapshot = _snapshot_for_run(
                run, snapshots, ActionWorkspace.load(home).binding
            )
            return review_document(run, snapshot)
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def decide_run(
    home: str | Path,
    run_id: str,
    *,
    approve: bool,
    confirm: Callable[[str], str],
    graph_store,
) -> dict[str, Any]:
    workspace = ActionWorkspace.load(home)
    try:
        with open_action_runtime(home, graph_store=graph_store) as (
            runtime,
            principal,
            snapshots,
            _tickets,
        ):
            runtime.gateway.require(principal, "read")
            run = runtime.run_service.get(run_id)
            if run.state.value != "AwaitingApproval":
                raise ActionWorkflowError(
                    "approval_state_invalid", "This Run is not awaiting a decision."
                )
            snapshot = _snapshot_for_run(run, snapshots, workspace.binding)
            _validate_snapshot_current(snapshot, runtime, graph_store)
            challenge = plan_digest(run)
            verb = "APPROVE" if approve else "REJECT"
            if not hmac.compare_digest(confirm(challenge), f"{verb} {challenge}"):
                raise ActionWorkflowError(
                    "operator_confirmation_failed",
                    "The exact Plan confirmation did not match.",
                )
            decision = ApprovalChoice.APPROVE if approve else ApprovalChoice.REJECT
            reason = (
                f"Local operator approved Plan {challenge}."
                if approve
                else f"Local operator rejected Plan {challenge}."
            )
            changed = runtime.run_service.decide(
                run_id,
                ApprovalDecisionRequest(decision, run.version, reason=reason),
            )
            return {
                "status": changed.state.value,
                "run": changed.to_dict(),
                "plan_digest": challenge,
            }
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def admit_run(home: str | Path, run_id: str, *, graph_store) -> dict[str, Any]:
    workspace = ActionWorkspace.load(home)
    try:
        with open_action_runtime(home, graph_store=graph_store) as (
            runtime,
            principal,
            snapshots,
            tickets,
        ):
            runtime.gateway.require(principal, "admit")
            run = runtime.run_service.get(run_id)
            if run.state.value != "Approved":
                raise ActionWorkflowError(
                    "run_not_approved", "Only an approved Run can be admitted."
                )
            snapshot = _snapshot_for_run(run, snapshots, workspace.binding)
            _validate_snapshot_current(snapshot, runtime, graph_store)
            command = AdmissionCommand(
                run.run_id,
                run.version,
                principal.subject_id,
                str(uuid4()),
            )
            admitted = runtime.gateway.admit(command, principal)
            return {
                "status": admitted.action.state.value,
                "replayed": admitted.replayed,
                "action": admitted.action.snapshot(),
                "ticket_count": tickets.count(),
            }
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def authorize_action(
    home: str | Path,
    action_id: str,
    *,
    confirm: Callable[[str], str],
    graph_store,
) -> dict[str, Any]:
    workspace = ActionWorkspace.load(home)
    try:
        with open_action_runtime(home, graph_store=graph_store) as (
            runtime,
            principal,
            snapshots,
            _tickets,
        ):
            runtime.gateway.require(principal, "authorize")
            action = runtime.gateway.get(action_id, principal)
            if action.state.value != "Pending":
                raise ActionWorkflowError(
                    "action_state_invalid", "Only a pending Action can be authorized."
                )
            snapshot = _snapshot_for_action(action, snapshots, workspace.binding)
            _validate_snapshot_current(snapshot, runtime, graph_store)
            challenge = f"AUTHORIZE {action.action_id}"
            if not hmac.compare_digest(confirm(action.action_id), challenge):
                raise ActionWorkflowError(
                    "operator_confirmation_failed",
                    "The exact Action authorization confirmation did not match.",
                )
            result = runtime.gateway.authorize(
                ActionCommand(action.action_id, action.version, str(uuid4())), principal
            )
            return {
                "status": result.action.state.value,
                "replayed": result.replayed,
                "action": result.action.snapshot(),
                "evidence_digest": snapshot["evidence_digest"],
            }
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def execute_action(
    home: str | Path,
    action_id: str,
    *,
    confirm: Callable[[str], str],
    graph_store,
) -> dict[str, Any]:
    workspace = ActionWorkspace.load(home)
    try:
        with open_action_runtime(home, graph_store=graph_store) as (
            runtime,
            principal,
            snapshots,
            tickets,
        ):
            runtime.gateway.require(principal, "execute")
            action = runtime.gateway.get(action_id, principal)
            if action.state.value != "Authorized":
                raise ActionWorkflowError(
                    "action_state_invalid",
                    "Only an independently authorized Action can execute.",
                )
            snapshot = _snapshot_for_action(action, snapshots, workspace.binding)
            challenge = f"EXECUTE {action.action_id}"
            if not hmac.compare_digest(confirm(action.action_id), challenge):
                raise ActionWorkflowError(
                    "operator_confirmation_failed",
                    "The exact Action execution confirmation did not match.",
                )
            _validate_snapshot_current(snapshot, runtime, graph_store)
            result = asyncio.run(
                runtime.gateway.execute(
                    ActionCommand(action.action_id, action.version, str(uuid4())),
                    principal,
                )
            )
            current_action = result.action
            receipt_id = (
                current_action.result.get("receipt_reference")
                if current_action.result is not None
                else None
            )
            receipt = tickets.get_receipt(receipt_id) if receipt_id else None
            verified = _receipt_matches_action(receipt, current_action)
            payload = {
                "status": current_action.state.value,
                "replayed": result.replayed,
                "action": current_action.snapshot(),
                "receipt_verified": verified,
            }
            if verified and receipt is not None:
                payload["ticket"] = {
                    "ticket_id": receipt.ticket_id,
                    "receipt_id": receipt.receipt_id,
                    "incident_id": receipt.incident_id,
                    "evidence_digest": receipt.evidence_digest,
                    "payload_digest": receipt.payload_digest,
                }
            elif current_action.state.value == "Unknown":
                possible = tickets.find_effect(
                    current_action.effect_key, _action_payload_digest(current_action)
                )
                payload["effect_observed_without_runtime_receipt"] = (
                    possible is not None
                )
            if current_action.state.value == "Succeeded" and not verified:
                raise ActionWorkflowError(
                    "receipt_verification_failed",
                    "Runtime reported success but the consumer receipt could not be independently verified.",
                )
            return payload
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def action_status(home: str | Path, action_id: str) -> dict[str, Any]:
    workspace = ActionWorkspace.load(home)
    try:
        with open_action_runtime(home) as (runtime, principal, snapshots, tickets):
            runtime.gateway.require(principal, "read")
            action = runtime.gateway.get(action_id, principal)
            snapshot = _snapshot_for_action(action, snapshots, workspace.binding)
            receipt_id = (
                action.result.get("receipt_reference") if action.result else None
            )
            receipt = tickets.get_receipt(receipt_id) if receipt_id else None
            receipt_verified = _receipt_matches_action(receipt, action)
            effect_observed = (
                tickets.find_effect(action.effect_key, _action_payload_digest(action))
                is not None
                if action.state.value in {"Unknown", "Failed"}
                else receipt_verified
            )
            return {
                "status": action.state.value,
                "action": action.snapshot(),
                "snapshot_age_seconds": (
                    ensure_snapshot_fresh(snapshot) if _is_fresh(snapshot) else None
                ),
                "snapshot_fresh": _is_fresh(snapshot),
                "evidence_digest": snapshot["evidence_digest"],
                "receipt_verified": receipt_verified,
                "effect_observed": effect_observed,
            }
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def list_tickets(home: str | Path) -> dict[str, Any]:
    try:
        with open_action_runtime(home) as (runtime, principal, _snapshots, tickets):
            runtime.gateway.require(principal, "read")
            values = tickets.list_tickets()
            return {"status": "ok", "count": len(values), "tickets": values}
    except ActionWorkflowError:
        raise
    except Exception as exc:
        raise _translate_runtime_error(exc) from None


def review_document(run, snapshot: dict[str, Any]) -> dict[str, Any]:
    if run.plan is None:
        raise ActionWorkflowError(
            "plan_missing", "The Run has no validated Plan to review."
        )
    age = _snapshot_age(snapshot)
    source_ages = source_data_ages(snapshot)
    return {
        "status": run.state.value,
        "run_id": run.run_id,
        "plan_digest": plan_digest(run),
        "plan": run.plan.to_dict(),
        "incident_id": snapshot["incident_id"],
        "action": TOOL_NAME,
        "target": run.plan.target,
        "evidence_digest": snapshot["evidence_digest"],
        "affected_services": [
            item["service_id"] for item in snapshot["affected_services"]
        ],
        "affected_service_count": len(snapshot["affected_services"]),
        "verified_dependency_paths": snapshot["verified_dependency_paths"],
        "deployment_evidence_ids": snapshot["deployment_evidence_ids"],
        "alert_evidence_ids": snapshot["alert_evidence_ids"],
        "reason": "Record the complete graph investigation as a local follow-up ticket; this performs no remediation.",
        "causality_status": snapshot["causality_status"],
        "risk_class": run.risk_class.value if run.risk_class else None,
        "snapshot_observed_at": snapshot["observed_at"],
        "snapshot_age_seconds": age,
        "snapshot_max_age_seconds": MAX_SNAPSHOT_AGE_SECONDS,
        "snapshot_fresh": age is not None and 0 <= age <= MAX_SNAPSHOT_AGE_SECONDS,
        "graph_identity": snapshot["graph_identity"],
        "source_data_timestamps": source_ages,
        "validation_note": "Freshness checks are point-in-time reads, not an atomic transaction across FalkorDB, Runtime SQLite, and ticket SQLite.",
        "approval_prompt": f"Type APPROVE {plan_digest(run)} to approve this exact Plan.",
    }


def plan_digest(run) -> str:
    if run.plan is None:
        raise ActionWorkflowError("plan_missing", "The Run has no validated Plan.")
    return hashlib.sha256(
        canonical_json(run.plan.to_dict()).encode("utf-8")
    ).hexdigest()


def action_arguments(snapshot: dict[str, Any]) -> dict[str, Any]:
    validate_snapshot(snapshot)
    return {
        "incident_id": snapshot["incident_id"],
        "evidence_digest": snapshot["evidence_digest"],
        "snapshot_observed_at": snapshot["observed_at"],
        "affected_service_count": len(snapshot["affected_services"]),
    }


def action_intent(snapshot: dict[str, Any]) -> str:
    validate_snapshot(snapshot)
    return (
        f"Create local incident ticket for {snapshot['incident_id']} "
        f"using evidence digest {snapshot['evidence_digest']}."
    )


def _snapshot_for_run(
    run, snapshots: EvidenceSnapshotStore, binding: ToolBinding
) -> dict[str, Any]:
    snapshot = snapshots.for_run(run.run_id)
    if snapshot is None:
        raise ActionWorkflowError(
            "snapshot_missing", "The Run has no linked trusted evidence snapshot."
        )
    validate_snapshot(snapshot)
    _validate_run_plan(run, snapshot, binding)
    return snapshot


def _snapshot_for_action(
    action, snapshots: EvidenceSnapshotStore, binding: ToolBinding
) -> dict[str, Any]:
    if not binding.matches(action.spec):
        raise ActionWorkflowError(
            "binding_mismatch",
            "The Action does not match the configured trusted binding.",
        )
    snapshot = snapshots.for_run(action.spec.run_id)
    if snapshot is None:
        raise ActionWorkflowError(
            "snapshot_missing", "The Action has no linked trusted evidence snapshot."
        )
    validate_snapshot(snapshot)
    arguments = dict(action.spec.normalized_arguments)
    if not _arguments_match_snapshot(arguments, snapshot):
        raise ActionWorkflowError(
            "snapshot_mismatch",
            "Action arguments do not match the approved evidence snapshot.",
        )
    if (
        action.spec.target != binding.target
        or action.spec.tool_name != TOOL_NAME
        or action.spec.tool_version != TOOL_VERSION
    ):
        raise ActionWorkflowError(
            "binding_mismatch",
            "The Action target or tool identity does not match the reviewed binding.",
        )
    return snapshot


def _validate_run_plan(run, snapshot: dict[str, Any], binding: ToolBinding) -> None:
    if run.plan is None:
        raise ActionWorkflowError("plan_missing", "The Run has no validated Plan.")
    plan = run.plan.to_dict()
    expected_args = action_arguments(snapshot)
    if (
        plan.get("target") != binding.target
        or plan.get("risk_class") != "SIDE_EFFECT"
        or len(plan.get("steps", [])) != 1
        or plan["steps"][0].get("tool_name") != TOOL_NAME
        or plan["steps"][0].get("tool_version") != TOOL_VERSION
        or plan["steps"][0].get("arguments") != expected_args
        or run.intent.text != action_intent(snapshot)
    ):
        raise ActionWorkflowError(
            "plan_mismatch",
            "The Runtime Plan does not match the trusted evidence snapshot.",
        )


def _validate_snapshot_current(snapshot, runtime, graph_store) -> None:
    try:
        ensure_snapshot_fresh(snapshot)
        current_evidence = IncidentRepository(graph_store).investigate(
            snapshot["incident_id"]
        )
        current = snapshot_from_graph_evidence(
            current_evidence,
            incident_id=snapshot["incident_id"],
            graph_identity=graph_store.settings.graph_name,
            observed_at=utc_now_text(),
        )
        if not snapshot_matches_current(snapshot, current):
            raise ActionWorkflowError(
                "evidence_changed",
                "Current graph evidence differs from the reviewed snapshot.",
            )
    except ActionWorkflowError:
        raise
    except (EvidenceSnapshotError, IncidentGraphError) as exc:
        raise ActionWorkflowError(
            "evidence_unavailable",
            "Current graph evidence is incomplete, stale, or unavailable.",
        ) from exc


def _arguments_match_snapshot(arguments: object, snapshot: dict[str, Any]) -> bool:
    try:
        return type(arguments) is dict and arguments == action_arguments(snapshot)
    except EvidenceSnapshotError:
        return False


def _argument_semantics(arguments, target):
    if (
        target is None
        or not isinstance(arguments, Mapping)
        or set(arguments) != set(ARGUMENT_SCHEMA["required"])
    ):
        return "invalid argument set"
    try:
        validate_identifier(arguments["incident_id"], field="incident ID")
        if not _DIGEST.fullmatch(arguments["evidence_digest"]):
            return "invalid evidence digest"
        if not _TIMESTAMP.fullmatch(arguments["snapshot_observed_at"]):
            return "invalid snapshot timestamp"
        parsed = datetime.strptime(
            arguments["snapshot_observed_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=UTC)
        if (
            type(arguments["affected_service_count"]) is not int
            or not 1 <= arguments["affected_service_count"] <= 500
        ):
            return "invalid affected-service count"
        if parsed.utcoffset() is None:
            return "timezone missing"
    except (IncidentGraphError, KeyError, TypeError, ValueError):
        return "invalid argument value"
    return None


def _valid_arguments(arguments: object) -> bool:
    return _argument_semantics(arguments, TARGET_PREFIX + "valid") is None


def _payload_digest(spec) -> str:
    digest = spec.payload_digest()
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise StoreOperationError("The Runtime payload digest could not be validated.")
    return digest


def _action_payload_digest(action) -> str:
    return _payload_digest(ExecutionSpec.from_compiled(action.spec))


def _receipt_matches_action(receipt: TicketReceipt | None, action) -> bool:
    if receipt is None or action.result is None:
        return False
    args = dict(action.spec.normalized_arguments)
    return (
        receipt.receipt_id == action.result.get("receipt_reference")
        and receipt.incident_id == args.get("incident_id")
        and receipt.evidence_digest == args.get("evidence_digest")
        and receipt.payload_digest == _action_payload_digest(action)
        and receipt.effect_key == action.effect_key
        and receipt.execution_scope == action.spec.execution_scope
        and receipt.target == action.spec.target
        and receipt.tool_name == action.spec.tool_name
        and receipt.tool_version == action.spec.tool_version
        and receipt.adapter_id == action.spec.adapter_id
        and receipt.binding_revision == action.spec.binding_revision
    )


def _snapshot_age(snapshot: dict[str, Any]) -> float | None:
    try:
        return ensure_snapshot_fresh(snapshot)
    except EvidenceSnapshotError:
        return None


def _is_fresh(snapshot: dict[str, Any]) -> bool:
    age = _snapshot_age(snapshot)
    return age is not None and 0 <= age <= MAX_SNAPSHOT_AGE_SECONDS


def _translate_runtime_error(exc: Exception) -> ActionWorkflowError:
    if isinstance(exc, ActionWorkflowError):
        return exc
    if isinstance(exc, IncidentGraphError):
        return ActionWorkflowError(exc.code, str(exc))
    code = getattr(exc, "code", None)
    code_value = getattr(code, "value", code)
    if isinstance(code_value, str) and re.fullmatch(
        r"[A-Z][A-Z0-9_]{1,63}", code_value
    ):
        return ActionWorkflowError(
            code_value,
            "The pinned Runtime denied or could not complete this lifecycle operation.",
        )
    if isinstance(exc, StoreIdentityMismatch):
        return ActionWorkflowError("store_identity_mismatch", str(exc))
    if isinstance(exc, StoreOperationError):
        return ActionWorkflowError("action_store_error", str(exc))
    return ActionWorkflowError(
        "action_operation_failed", "The local action operation failed closed."
    )


def _write_private_new_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
