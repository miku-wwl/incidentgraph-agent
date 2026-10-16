# Phase 3.1 — CLI Confirmation Fix and Regression

- **Baseline:** `1587e2c493217812b222e37f6f3d3bca79365c6b`
- **Result:** **PASS**
**Phase 4:** Not started; independent security review remains required.

## Finding and root cause

The finding was confirmed. `_run_action_command()` displayed `Type {challenge}:`, but its callback receives only a Plan digest for approval or an Action ID for authorization/execution. The unchanged workflow checks require the full phrases `APPROVE <plan-digest>`, `AUTHORIZE <action-id>`, and `EXECUTE <action-id>` with exact `hmac.compare_digest` comparison.

Before the fix, a mocked invocation of `incidentgraph action approve` produced:

```text
Prompt:  'Type abc123: '
Expected: 'APPROVE abc123'
Prompt contained expected phrase: False
```

The review document and README already described the required complete phrases. The defect was only in the interactive CLI prompt.

## Minimal fix

The CLI now selects the fixed verb for the current operation and displays `Type exactly:` followed by the complete phrase on the next line. The callback still returns the user's unmodified input. `ticket_actions.py`, its exact comparison, Runtime approval, authorization, execution, and ledger semantics were not changed. No automatic confirmation, default-yes behavior, flag, or model consent path was added. README instructions required no correction.

After the fix, the mocked CLI tests capture these prompts exactly:

```text
Type exactly:
APPROVE plan-digest-123

Type exactly:
AUTHORIZE action-123

Type exactly:
EXECUTE action-123
```

## Regression coverage and results

- Mocked `input` verifies the approval, authorization, and execution prompt text and the unmodified phrase passed to each workflow.
- Existing end-to-end Runtime lifecycle accepts exact approval, authorization, and execution phrases using test-owned confirmation callbacks.
- Isolated ticket tests reject a missing prefix, wrong Plan digest, wrong Action ID, and empty confirmation. They verify rejected approval remains `AwaitingApproval`, rejected authorization remains `Pending`, rejected execution remains `Authorized`, and no ticket is written.
- Mocked EOF and `KeyboardInterrupt` produce the controlled `operator_confirmation_cancelled` JSON response.
- The initial prompt regression run failed all three new prompt cases against the old CLI, reproducing the defect.

| Validation | Result |
| --- | --- |
| `uv run --locked --env-file .env --python 3.13 pytest -q tests/test_action_cli.py tests/test_action_evidence.py tests/test_ticket_actions.py` | PASS — 42 passed in 58.49s |
| `uv run --locked --env-file .env --python 3.13 pytest -q` | PASS — 149 passed in 91.59s; includes real FalkorDB/MCP integration |
| `uv run --locked --python 3.13 ruff check src/incidentgraph_agent tests` | PASS |
| `uv run --locked --python 3.13 black --check src/incidentgraph_agent/cli.py tests/test_action_cli.py tests/test_ticket_actions.py` | PASS — 3 files unchanged |
| `uv sync --locked` | PASS |
| `uv pip check --python .venv\Scripts\python.exe` | PASS — all installed packages compatible |
| `git diff --check` | PASS |

## Compatibility and scope checks

- Installed `agentic-intent-gate==0.2.0` remains sourced from `.venv\Lib\site-packages`, pinned to `7261fdd2f47d2b15fabe8faac527eeee37338eb1`.
- `compose.yaml` remains unchanged and still references `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0`.
- Agent and MCP modules are unchanged; MCP retains only its four graph-read tools. No new authorization path exists.
- No persistent demonstration ticket, unrelated Docker change, or shared Runtime modification was made.

## Files changed and remaining limitations

Changed: `src/incidentgraph_agent/cli.py`, `tests/test_action_cli.py`, `tests/test_ticket_actions.py`, and this report. `README.md`, historical reports, dependency files, Agent, MCP, and Runtime sources were not changed.

The local CLI prompt and capability are still demonstration controls, not production multi-user authentication. The pinned Runtime still has no public cancellation operation for an already admitted Action. No persistent manual demo was run. These Phase 3 limitations remain for the independent reviewer to assess.

## Security review readiness

**Ready for independent Phase 3 security review.** This patch changes only confirmation text and regression coverage; Phase 4 remains **NO-GO** until that review is complete.

## Git status

Branch `main` remains at baseline `1587e2c493217812b222e37f6f3d3bca79365c6b` before this patch. The four files above are local uncommitted changes. No commit or push was performed.
