# Graph Hacks — Phase 3 Report

- **Baseline:** `50a2092e00558ebb3d9c5beafea47fe85d4ef5ca`
- **Result:** **PASS** for the approved local-only action scope.
- **Phase 4:** **NO-GO** until independent security review.

## 1. Architecture implemented

Phase 3 adds one consumer-owned `SIDE_EFFECT` capability, `create_local_incident_ticket` v1. The existing Phase 2.1 Agent and read-only graph tools produce the investigation; trusted consumer code validates it, persists an evidence snapshot, and asks the pinned Agentic Intent Gate Runtime to manage the Plan/Run/Action lifecycle. The executor writes one incident ticket and receipt to a separate local SQLite store. No Agent or MCP write tool was added.

The action workspace uses separate `gate.sqlite3`, `snapshots.sqlite3`, and `tickets.sqlite3` files, plus private identity and capability files. It uses a fresh UUIDv4 execution scope, persists its binding identity, and retains the default five-second Runtime execution budget. Provisioning occurs only through the explicit `action init` command.

## 2. Runtime APIs used

The installed `agentic-intent-gate==0.2.0` is imported from the project `.venv` and resolves to commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`. The consumer uses public exports: `ToolBinding`, `ToolCapability`, `ExecutableRegistry`, `CapabilityVerifier`, `BoundCapabilityAuthorization`, `Intent`, `ApprovalDecisionRequest`, `ApprovalChoice`, `AdmissionCommand`, `ActionCommand`, `ToolOutcome`, `ExecutionSpec`, `open_runtime`, and `provision_capability`.

The tested integration uses the public Run service and Gateway for approval, admission, authorization, and execution. No shared Runtime source or internal implementation was copied or modified. The dependency's `direct_url.json` identifies the exact pinned commit.

## 3. Action Plan and evidence binding

The proposal path accepts only a completed Phase 2.1 investigation for the exact incident: `task_completed=true`, `evidence_complete=true`, deterministic answer source, one matching `investigate_incident` observation, complete non-truncated paths, and required evidence references. A validated canonical snapshot records incident identity and state, involved/relevant/affected service IDs, verified paths, deployment and alert IDs/timestamps, graph identity, unproven causality, and observation time.

The snapshot SHA-256 binds canonical content and `observed_at`; stored snapshots validate against their digest after reopening. Model prose, tool timing, and random request IDs are excluded. Snapshots expire after ten minutes. Source-data ages are displayed separately. Before approval, admission, authorization, and execution, trusted code re-queries graph evidence and rejects incomplete, changed, stale, or differently identified evidence. These checks are point-in-time checks across separate databases, not an atomic cross-database transaction or a universal freshness guarantee.

The deterministic consumer `ModelProvider` creates one Plan with four flat arguments: incident ID, evidence digest, observation timestamp, and affected-service count. It cannot choose a target, binding, scope, credential, budget, or consent decision.

## 4. Human approval separation

`propose` only investigates and creates an `AwaitingApproval` Run. `review` shows the Plan digest, target, risk, affected services and paths, evidence references, snapshot age, source timestamps, and causality uncertainty. `approve` or `reject` is a separate command and requires the exact displayed Plan challenge. Rejection is terminal and is not admitted. A mismatched challenge leaves the Run awaiting approval. Automated tests use test-owned consent callbacks; they are not evidence of human consent.

## 5. Independent Authorization

After approval, `admit` is a distinct Runtime operation. `authorize` then requires a separate exact `AUTHORIZE <action-id>` confirmation. An Action cannot execute without authorization. `execute` is another command and requires exact `EXECUTE <action-id>` confirmation; it does not create a new Plan. The Agent and MCP tools have no approval, admission, authorization, or execution operation.

## 6. Ticket executor and receipt evidence

The registered executor checks the complete Runtime `ExecutionSpec`, exact tool/version/target/adapter/binding/scope, action arguments, stored snapshot identity and digest, snapshot age, and a fresh complete graph read before mutation. It inserts a ticket and receipt through SQLite, then reads the row back and validates the receipt against the Runtime Action, effect key, payload digest, evidence digest, and binding identity. A malformed or unverifiable receipt is not reported as success.

## 7. SQLite durability and idempotency

Each SQLite connection applies `busy_timeout=1000`, foreign-key enforcement, and `synchronous=FULL`. Ticket creation uses `BEGIN IMMEDIATE`, unique ticket/receipt/effect identities, an effect-key/payload uniqueness constraint, commit, and read-back verification. The same effect key and payload returns its existing receipt; a different payload is rejected. A commit with uncertain acknowledgment yields `Unknown`; execution does not retry it.

`test_operator_lifecycle_uses_distinct_gates_and_persists_one_ticket` closes the Runtime/store handles, starts a separate Python process, reopens the persisted state, and verifies the Action remains `Succeeded`, one ticket remains, and another execution is rejected with `action_state_invalid`. `test_concurrent_gateway_execution_creates_one_ticket` sends two commands through the real Runtime Gateway and verifies exactly one successful Action and one ticket. `test_lost_ticket_commit_ack_is_unknown_and_never_retried` verifies `Unknown`, one durable ticket, and no replay. `test_cancelled_execution_confirmation_does_not_dispatch_action` verifies cancellation leaves the Action `Authorized` and creates no ticket. These results concern the local SQLite demonstration only; they do not claim exactly-once remote effects.

## 8. Cancellation boundary

Canceling the interactive execution confirmation before dispatch leaves the Action `Authorized` and creates no ticket. The installed v0.2.0 public API exposes no cancellation operation for an already admitted Action; this consumer does not add a private Runtime workaround. A user can reject before admission. Once admitted, terminal state and Runtime policy govern the Action.

## 9. Real FalkorDB integration

The full suite ran against the configured local FalkorDB 6.0.2 service. Tests use a unique `incidentgraph_test_<uuid>` graph per test and delete only that generated graph. Phase 3 tests investigate the synthetic incident through FalkorDB, validate graph-backed evidence, build snapshots and proposals, and exercise changed/stale/incomplete evidence denial. Existing graph traversal and MCP integration tests also passed. No project graph data was modified for the manual demonstration; no Docker resources were changed during Phase 3.

## 10. Agent and MCP regression

The full suite includes the existing Task Contract, deterministic summary, provider timeout/byte-limit, graph completeness, MCP discovery/call, and read-only tool tests. MCP continues to expose exactly the four existing graph-read tools; it has no ticket or Action write tool. A real local Ollama read-only smoke ran:

```powershell
uv run --locked --env-file .env --python 3.13 incidentgraph agent ask "Investigate incident-payment-001 and explain which services may be affected."
```

Observed result: `phi4-mini:latest`, status `complete`, `task_completed=true`, `evidence_complete=true`, `answer_source=deterministic`, `model_answer_grounded=false`; one `investigate_incident` call returned the expected incident and dependency paths. No approval, Action, or ticket was created by this read-only command.

## 11. Executed validation

| Command | Result |
| --- | --- |
| `uv sync --locked --python 3.13` | PASS; lock resolved and installed environment checked |
| `uv pip check --python .venv\Scripts\python.exe` | PASS; all installed packages compatible |
| Runtime version/source inspection | PASS; `0.2.0`, pinned commit above, imported from `.venv\Lib\site-packages` |
| `uv run --locked --env-file .env --python 3.13 pytest -q tests/test_action_cli.py tests/test_action_evidence.py tests/test_ticket_actions.py` | PASS; 29 passed |
| `uv run --locked --env-file .env --python 3.13 pytest -q` | PASS; 136 passed, including real FalkorDB and MCP integration |
| `uv run --locked --python 3.13 ruff check src/incidentgraph_agent tests` | PASS; all checks passed |
| `uv run --locked --python 3.13 black --check src/incidentgraph_agent/cli.py src/incidentgraph_agent/action_evidence.py src/incidentgraph_agent/action_store.py src/incidentgraph_agent/ticket_actions.py tests/test_action_cli.py tests/test_action_evidence.py tests/test_ticket_actions.py` | PASS; all seven unchanged |
| `git diff --check` | PASS; no whitespace errors |

## 12. Security limits and remaining risks

- The CLI consent prompt and local capability file demonstrate separation of gates; they are not authenticated multi-user approval.
- Graph validation and SQLite mutation cannot share one transaction. A graph change immediately after the last re-query remains possible.
- An uncertain local commit remains `Unknown`; there is no automated retry or reconciliation path.
- The installed Runtime has no public admitted-Action cancellation API.
- The manual interactive approval/execution demonstration was **NOT RUN**. No persistent demo ticket was created.
- No external ticketing, cloud, Kubernetes, service restart, or remediation effect is implemented.

## 13. Files created or modified

Modified: `README.md`, `src/incidentgraph_agent/cli.py`, `tests/test_ticket_actions.py`.
Created: `docs/PHASE3-REPORT.md`, `src/incidentgraph_agent/action_evidence.py`, `src/incidentgraph_agent/action_store.py`, `src/incidentgraph_agent/ticket_actions.py`, `tests/test_action_cli.py`, `tests/test_action_evidence.py`.

Historical Phase 0, Phase 1, Phase 1.1, Phase 1.2, Phase 2, and Phase 2.1 reports were not modified. `compose.yaml`, `pyproject.toml`, and `uv.lock` were not changed. The FalkorDB reference remains `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0`; the installed shared Runtime remains on the pinned commit.

## 14. Manual demonstration

The concrete operator walkthrough is in the README section [Phase 3 setup and manual walkthrough](../README.md#phase-3-setup-and-manual-walkthrough). It initializes a new local action home, proposes and reviews one incident, then requires separate interactive approval, admission, authorization, and execution. Each displayed ID must be copied from the preceding JSON output. The walkthrough is documentation only and must be performed by the operator; it has not been run as part of this report.

## 15. Phase 4 readiness

**NO-GO pending independent security review.** Phase 3 implementation and automated acceptance evidence are complete within the local-only scope. Do not start Phase 4 until the independent reviewer evaluates the new executor, persistent identity/storage boundaries, Runtime integration, and documented limitations.

## 16. Git status

The branch remains `main` at baseline commit `50a2092e00558ebb3d9c5beafea47fe85d4ef5ca`. Phase 3 source, tests, and documentation are local uncommitted changes. No commit, push, tag, release, or deployment was performed.
