# Graph Hacks — Phase 3.2 Final Execution Safety Fixes

- **Baseline:** `1587e2c493217812b222e37f6f3d3bca79365c6b`
- **Result:** **PASS** for the targeted local ticket Action safety fixes.
- **Phase 4:** **NO-GO** pending independent verification of this report and review ZIP.
- **Runtime:** installed `agentic-intent-gate==0.2.0`, pinned to `7261fdd2f47d2b15fabe8faac527eeee37338eb1`.

## Review baseline and scope

This report follows the latest corrected independent Sol review supplied with the task. The review reports are not separate files in the baseline `docs/` directory. The corrected review withdraws the earlier shallow-depth issue as a defect because `create_action_proposal()` re-queries with the default depth and rejects changed evidence. A regression test exercises that actual proposal entry point; no graph-depth or proposal design change was made.

Only the two confirmed P2 findings and the README P3 defect were changed. Existing uncommitted Phase 3.1 work was preserved. The pinned Runtime, its approval and authorization behavior, the Agent, MCP permissions, dependency lock, FalkorDB image, and Phase 4 scope were not changed.

## Findings and fixes

### P2-F1 — post-commit outcome classification

Before the fix, `LocalIncidentTicketExecutor.execute()` used the same exception mapping for pre-insert validation and `create_or_get()` readback. Since `create_or_get()` commits before calling `find_effect()`, a `StoreIdentityMismatch` or `EffectPayloadConflict` from that readback could be returned as `CONFIRMED_FAILURE` after one ticket was durable.

The executor now separates evidence validation from the call that can write the ticket. The store wraps any exception from post-commit receipt readback as `StoreOutcomeUnknown`. The executor preserves confirmed failures for known pre-effect identity and payload conflicts, reports uncertain commit/readback as `UNKNOWN_OUTCOME`, and reports success only after a matching durable receipt is returned. No automatic replay was added.

Fault-injection evidence: after the actual SQLite commit, a second `find_effect()` call was made to raise `StoreIdentityMismatch` and `EffectPayloadConflict` in separate runs. Both runs persisted exactly one ticket, produced a Runtime `Unknown` Action, exposed the observed effect through persisted status, and rejected another execution attempt without adding a ticket. Pre-effect identity and payload rejection tests still produce `Failed` with no new ticket. The existing successful lifecycle verifies one matching receipt. The lost-commit-acknowledgment test verifies persisted `Unknown`, one ticket, and replay denial.

**Updated risk:** the confirmed misclassification is fixed in the consumer path and covered by real Runtime/SQLite fault injection. A process termination at an arbitrary point still relies on the Runtime's durable recovery behavior; this report does not claim atomicity across Runtime and ticket databases.

### P2-F2 — interruption classification

Before the fix, the outer CLI handler labeled every `EOFError` or `KeyboardInterrupt` as confirmation cancellation, including interruption after execution confirmation. The prompt callback now catches EOF/Ctrl+C before it returns and raises `operator_confirmation_cancelled`. A later `KeyboardInterrupt` during `execute` returns `execution_interrupted`, with the Action ID, action home, status command, ticket-list command, and instructions not to retry. It does not claim a verified terminal state.

Tests verify prompt-stage EOF and Ctrl+C leave an authorized Action unchanged with zero tickets. A controlled real `signal.raise_signal(SIGINT)` was injected after `create_or_get()` had committed and read back the ticket, while the Runtime execution was still in flight. The CLI reported `execution_interrupted`; reopening the persisted workspace showed `Unknown`, `effect_observed=true`, and exactly one ticket. A repeat execution was denied. A separate interruption before dispatch remained `Authorized` with zero tickets while the CLI described the outcome conservatively.

**Updated risk:** interruption handling now distinguishes user cancellation from an in-flight execute interruption. The signal test uses a real Python SIGINT raised at a controlled post-commit point; it does not emulate every possible Windows terminal timing or forced process termination.

### P3 — README accuracy

The obsolete statement that the project does not compose or execute Runtime Actions was removed. README now describes the public Runtime composition, one trusted local SQLite ticket effect, separate human Plan Approval / Action Authorization / exact Execution Confirmation, read-only Agent/MCP graph tools, and no external remediation. It also explains how to inspect Action status and ticket receipts after interruption and warns against retrying an uncertain effect.

## Shallow evidence boundary regression

The test builds a valid depth-1 snapshot and calls the actual `create_action_proposal()` entry point. Current graph evidence differs at its default depth, so proposal creation fails with `evidence_changed`; no ticket is created. This is regression evidence for the corrected review's withdrawn finding, not a new finding or architecture change.

## Tests and validation actually executed

| Command | Result |
| --- | --- |
| `uv run --locked --no-sync --env-file .env --python 3.13 -m pytest -q -p no:cacheprovider tests/test_ticket_actions.py::test_lost_ticket_commit_ack_is_unknown_and_never_retried tests/test_ticket_actions.py::test_post_commit_readback_rejection_is_unknown_and_never_replayed tests/test_ticket_actions.py::test_pre_effect_store_rejection_remains_confirmed_failure tests/test_ticket_actions.py::test_shallow_snapshot_is_rejected_at_action_proposal_entrypoint tests/test_ticket_actions.py::test_cli_reports_sigint_after_committed_effect_as_ambiguous tests/test_ticket_actions.py::test_cli_execution_interrupt_before_dispatch_is_conservative tests/test_action_cli.py::test_cli_confirmation_cancellation_is_controlled` | PASS — 10 passed |
| `uv run --locked --no-sync --env-file .env --python 3.13 -m pytest -q -p no:cacheprovider tests/test_ticket_actions.py::test_existing_pre_effect_payload_conflict_is_rejected_without_new_ticket` | PASS — 1 passed; real existing effect conflict |
| `uv run --locked --no-sync --env-file .env --python 3.13 -m pytest -q -p no:cacheprovider tests/test_action_cli.py tests/test_action_evidence.py tests/test_ticket_actions.py` | PASS — 52 passed in 83.74s |
| `uv run --locked --no-sync --env-file .env --python 3.13 -m pytest -q -p no:cacheprovider` | PASS — 159 passed in 98.04s; real FalkorDB and stdio MCP integration included |
| Same full pytest command with the nine test modules explicitly listed in reverse module order | PASS — 159 passed in 97.99s |
| `uv run --locked --no-sync --python 3.13 ruff check src/incidentgraph_agent tests` | PASS — all checks passed |
| `uv run --locked --no-sync --python 3.13 black --check src/incidentgraph_agent/action_store.py src/incidentgraph_agent/ticket_actions.py src/incidentgraph_agent/cli.py tests/test_ticket_actions.py tests/test_action_cli.py` | PASS — 5 files unchanged |
| `uv sync --locked --python 3.13` | PASS — checked 46 packages |
| `uv pip check --python .venv\Scripts\python.exe` | PASS — all installed packages compatible |
| `git diff --check` | PASS |

An earlier full focused run reported `48 passed, 1 failed` because assertions for the lost-acknowledgment case had been placed in the pre-dispatch-interruption test. The test assertions were corrected; the targeted run, final focused run, and both final full-suite orders above passed.

The reverse-order command was: `uv run --locked --no-sync --env-file .env --python 3.13 -m pytest -q -p no:cacheprovider tests/test_ticket_actions.py tests/test_resultset_safety.py tests/test_mcp_integration.py tests/test_graph_tools.py tests/test_graph_integration.py tests/test_evidence_validation.py tests/test_agent.py tests/test_action_evidence.py tests/test_action_cli.py`.

The live Docker context was `desktop-linux` with Linux/amd64 Engine 29.8.0. The existing project FalkorDB container was healthy and used the pinned image; tests used function-scoped unique graphs. The real MCP stdio integration discovered exactly four graph-read tools and successfully called a read tool. No persistent ticket/demo action was created.

## Files changed

- `README.md`
- `src/incidentgraph_agent/action_store.py`
- `src/incidentgraph_agent/ticket_actions.py`
- `src/incidentgraph_agent/cli.py`
- `tests/test_ticket_actions.py`
- `tests/test_action_cli.py`
- `docs/PHASE3.2-REPORT.md` (this report)

The existing local uncommitted Phase 3.1 report and tests were preserved. No dependency, Runtime source, Agent, MCP, compose, graph business data, or Phase 4 implementation changes were made.

## Remaining limitations and readiness

- Runtime ledger state and ticket receipt state remain in separate SQLite databases and cannot commit atomically together.
- CLI capability files and local confirmation are demonstration controls, not production multi-user authentication.
- A real terminal Ctrl+C was represented by Python's `SIGINT` signal at a controlled post-commit point; power loss and arbitrary process termination were not injected in this phase.
- Only the local SQLite ticket effect is supported; no external remediation or irreversible third-party effect was run.

**Phase 3.2 targeted fixes: PASS. Phase 4: NO-GO until independent review approves the new code and evidence.**
