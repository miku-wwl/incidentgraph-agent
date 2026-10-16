# Graph Hacks — Phase 2.1 Report

- Verification date: 2026-10-16 NZDT
- Baseline: `1293b6b7f952f83bb5fc924c608d4404ecbcf1eb`
- Result: **PHASE 2.1: PASS — task completion and provider boundaries hardened**
- Phase 3: **NO-GO pending independent review and separate user approval**

## 1. Root causes and fixes

### Request completion was not bound to the requested target

Previously, any successful graph observation could lead to `status: complete`; the request's target and the returned graph identity were not independently matched. The Agent now builds an application-controlled Task Contract before calling the model. It supports explicit incident investigations, service dependency requests, and blast-radius requests. It records each target, required tool, and evidence fields. Multiple explicit targets are separate requirements; ambiguous and unsupported requests return `unsupported_task` without calling the model. Requests requiring more than the four-call limit are rejected.

Every required observation must use the exact tool and target in its arguments, carry `evidence_complete: true`, and return the same target identity. Incident evidence must include the incident and its impact, alert, and deployment collections. Dependency and blast-radius results must include structurally valid paths and complete count/target fields. A dependency lookup cannot fulfill an incident investigation. All user-facing completion decisions use this same validator.

### Model prose was incorrectly treated as grounded

Identifier, citation, vocabulary, and English-pattern checks could not prove arbitrary claims, including claims in Chinese or claims about an unobserved path. Model final text is now treated as untrusted protocol content and is never returned to the user. The deterministic renderer creates an answer only after every Task Contract requirement is matched, and it reads only those matching structured observations. Alert and incident descriptions remain data and are not copied into trusted answer text.

The response schema now defines `task_completed`, `evidence_complete`, `answer_source`, `model_answer_grounded`, and `causality.status` separately. `task_completed` requires every contract target to match. `evidence_complete` reports completeness of returned graph observations and can be true even when they do not satisfy the requested task. `answer_source` is `deterministic` only when a trusted summary exists. `model_answer_grounded` remains false because model prose is never used. Incident requests retain `causality.status: unproven`; service-only requests use `not_assessed`.

### Ollama limits did not bound total time or bytes while reading

The provider previously used eager response reads and per-request HTTP timeouts. It now uses a monotonic deadline shared by model discovery and completion, finite connect/read/write/pool timeouts, and streamed reads. Both `/models` and `/chat/completions` responses are capped at 64,000 bytes before JSON parsing. The provider disables redirects and verifies injected clients use the configured loopback endpoint. It does not retry or use a remote fallback.

### Malformed `/models` payloads could raise `TypeError`

The provider now requires an object with a list-valued `data` field and nonempty string `id` fields in every model record. Missing fields, unexpected response types, and malformed records produce the controlled `model_unavailable` error.

## 2. Files changed

- `src/incidentgraph_agent/agent.py` — Task Contract, target/evidence matcher, deterministic answer boundary, streamed provider limits, strict model-list validation.
- `tests/test_agent.py` — target, answer trust, Unicode, injection, model-list, byte-limit, and deadline regressions.
- `README.md` — current Agent completion and provider behavior.
- `docs/PHASE2.1-REPORT.md` — this evidence report.

No graph repository, MCP interface, shared Runtime, dependency manifest, lockfile, Docker image, persistent graph, or Phase 3 code was changed. Integration fixtures create a unique graph for each test and delete only that test graph during teardown.

## 3. Regression coverage

The new tests cover the independent review reproductions:

- A request for a missing incident cannot be completed by investigating a different existing incident.
- A dependency-only lookup cannot complete an incident investigation.
- Every explicit incident/dependency target must be satisfied; ambiguous extra targets fail closed.
- A tool result whose returned incident identity differs from its arguments and the contract cannot complete the task.
- Valid citations do not authorize unsupported impact claims; Chinese root-cause/remediation text, a reversed unobserved path, and fullwidth Unicode text are discarded. An escaped lone surrogate returns `invalid_model_response`.
- Instruction-like alert descriptions are passed to the model as data but are absent from the trusted deterministic answer.
- Exact incident, dependency, and blast-radius requests can complete from matching structured results; the existing four-tool allowlist and execution budget remain tested.
- Controlled local HTTP streaming tests cover slow `/models`, slow `/chat/completions`, a deadline spanning both requests, streamed bodies over the byte cap for both endpoints, and malformed model-list variants including `{"data": 42}`.
- Existing real stdio MCP tests continue to exercise only the four read-only tools.

## 4. Executed validation

Commands and actual results:

```text
uv sync --locked --python 3.13
Resolved 47 packages; checked 46 packages

uv pip check --python .venv\Scripts\python.exe
All installed packages are compatible

uv run --locked --python 3.13 python -c "..."
agentic-intent-gate 0.2.0
direct_url commit_id=7261fdd2f47d2b15fabe8faac527eeee37338eb1
runtime imported from .venv\Lib\site-packages\agentic_intent_gate\runtime.py

uv run --locked --python 3.13 ruff check src/incidentgraph_agent tests
All checks passed

uv run --locked --python 3.13 black --check src/incidentgraph_agent/agent.py tests/test_agent.py
2 files would be left unchanged

uv run --locked --python 3.13 pytest -q tests/test_agent.py tests/test_graph_tools.py
57 passed in 2.31s

uv run --locked --env-file .env --python 3.13 pytest -q
107 passed in 14.88s

uv run --locked --env-file .env --python 3.13 pytest -q tests/test_resultset_safety.py tests/test_mcp_integration.py tests/test_graph_tools.py tests/test_graph_integration.py tests/test_evidence_validation.py tests/test_agent.py
107 passed in 16.28s

uv run --locked --env-file .env --python 3.13 pytest -q tests/test_graph_integration.py tests/test_mcp_integration.py
20 passed in 14.81s
```

The real integration runs used the healthy project FalkorDB container. Docker reported `Server=29.8.0`, `OSType=linux`, and `Architecture=x86_64`; the container uses `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0` and reported `Health=healthy`. The 20-test integration subset includes real FalkorDB queries and the MCP stdio client/tool validation. Each fixture uses and deletes a unique test graph.

The first real `phi4-mini` attempt was **not run**: `ollama list` timed out while detecting an existing Ollama app instance, and `http://127.0.0.1:11434/api/tags` refused the connection. After the user confirmed that the local model was resident, the read-only API and investigation were retried on 2026-10-16 NZDT:

```text
GET http://127.0.0.1:11434/api/version -> 0.35.1
GET http://127.0.0.1:11434/api/tags -> phi4-mini:latest
GET http://127.0.0.1:11434/api/ps -> phi4-mini:latest, size_vram=0,
  expires_at=2319-01-26T14:23:25.569413107+13:00
GET http://127.0.0.1:11434/v1/models -> phi4-mini:latest

uv run --locked --env-file .env --python 3.13 incidentgraph agent ask "Investigate incident-payment-001 and explain which services may be affected."
exit code: 0 (completed in about 52 seconds)
model: phi4-mini:latest
status=complete, task_completed=true, evidence_complete=true
tool_execution_count=1, tool=investigate_incident, tool status=success
answer_source=deterministic, model_answer_grounded=false
potentially impacted service: notification-service
verified paths: notification-service -> checkout-api;
  notification-service -> checkout-api -> payment-service
causality.status=unproven
```

This was one real local-model-backed, read-only graph investigation against the project FalkorDB dataset. The trusted answer came from validated structured graph evidence; model prose was not returned. The result identifies `notification-service` as potentially impacted and explicitly treats deployment/alert timing as correlation, not proof of cause.

`black --check src/incidentgraph_agent tests` was also run and reported that the untouched `src/incidentgraph_agent/seed.py` would be reformatted. The changed Python files pass the targeted Black check; `seed.py` was left unchanged as unrelated scope.

## 5. Remaining limitations and risks

- Task Contract recognition is intentionally narrow and supports explicit English incident, dependency, and blast-radius requests. Unsupported or ambiguous wording returns a controlled non-complete result; it does not guess the user's target.
- Deterministic answers summarize validated graph structures, but the source graph is synthetic and can be stale or inaccurate. `evidence_complete` does not prove source freshness, ground truth, or causality.
- The live `phi4-mini` smoke test covered one explicit incident investigation and confirmed the provider/tool/evidence path; it does not establish quality across other prompts or requests. The local controlled streaming endpoint separately exercises provider timeout and size handling.
- A repository-wide Black check remains blocked by pre-existing formatting in the untouched `seed.py`; the changed files pass Black.
- The Agent remains read-only. It does not compose Agentic Intent Gate Runtime Actions or authorize remediation.

**Phase 3 readiness: NO-GO** until independent review and explicit user approval. No commit, push, deployment, Runtime modification, or persistent graph mutation was performed during the Phase 2.1 implementation and validation recorded above.
