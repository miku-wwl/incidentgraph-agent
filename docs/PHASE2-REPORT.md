# Graph Hacks — Phase 2 Final Report

- Verification date: 2026-10-15 NZDT
- Baseline: `75c7680aa6c00a17068a6379eaaec8f5622d933f`
- Result: **PHASE 2: PASS — bounded Agent, trusted graph tools, and local stdio MCP verified**
- Phase 3: **NO-GO pending independent review and separate user approval**

## 1. Architecture

Phase 2 adds a small product-owned loop around the existing `IncidentRepository`:

```text
User request
  → local Ollama JSON decision
  → strict decision and argument validation
  → fixed read-only graph tool registry
  → existing IncidentRepository and FalkorDB
  → validated evidence plus stable evidence references
  → grounded model explanation or deterministic evidence summary
```

There is no second query layer. The model does not receive a database client and cannot supply Cypher, query templates, URLs, credentials, shell commands, or tool implementations. Existing graph query limits, evidence validation, `incomplete_graph_evidence` behavior, and `RESULTSET_SIZE` checks remain in force.

The shared Agentic Intent Gate was not integrated into this read-only phase and was not modified. The installed package remains `agentic-intent-gate==0.2.0` from commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`, imported from the project `.venv`. It remains pinned for a separately reviewed Phase 3 use.

## 2. Trusted graph tools

The fixed registry in `src/incidentgraph_agent/graph_tools.py` maps exactly four names to existing repository methods:

| Tool | Repository call |
| --- | --- |
| `get_service_dependencies` | `dependencies(service_id, max_depth=...)` |
| `calculate_blast_radius` | `blast_radius(failed_service_id, max_depth=...)` |
| `correlate_incident_evidence` | `correlate(incident_id=...)` |
| `investigate_incident` | `investigate(incident_id, max_depth=...)` |

Argument objects reject missing fields, extra fields, invalid identifiers, booleans in integer positions, and traversal depth outside 1–4. A result without `evidence_complete: true` raises `incomplete_graph_evidence`. Existing repository errors retain their codes, including `database_unavailable`, `invalid_input`, `not_found`, `graph_query_failed`, and `incomplete_graph_evidence`.

Each result includes stable references such as `incident:incident-payment-001`, `alert:alert-payment-5xx-001`, `deployment:deploy-payment-2026-10-15`, `service:notification-service`, and `path:notification-service>checkout-api`.

## 3. Agent loop and Ollama

`incidentgraph agent ask "..."` uses explicit `call_tool` and `final` decisions, with the exact allowlisted tool names as a strict tool-decision form for local model compatibility. JSON duplicate keys, unsupported fields, malformed output, invalid tools, unobserved final answers, and unknown citations fail closed. The loop permits at most four graph-tool executions. Each Ollama request has a finite configurable timeout; request, context, and model response sizes are bounded. No retries, cloud fallback, or model download occurs.

The provider uses the OpenAI-compatible API at `http://127.0.0.1:11434/v1` by default, verifies the selected identifier against `/models`, requires a loopback `/v1` URL, and disables proxy environment use. The selected local model was `phi4-mini:latest`. The pinned environment contains `httpx==0.28.1`; `OLLAMA_TIMEOUT_SECONDS` defaults to 45 and is limited to 0.1–120 seconds.

Final model text is accepted only when its citations and identifiers are observed, path claims exactly match observed paths, causal wording is safe, and the text uses the constrained narrative vocabulary. Otherwise, the application builds a deterministic summary from the actual graph result. The returned JSON always retains the full structured observations, tool trace, stable references, uncertainty, and causality status.

### Real Ollama investigation

Command:

```text
uv run --locked --env-file .env --python 3.13 incidentgraph agent ask "Investigate incident-payment-001 and explain which services may be affected."
```

Actual result (the CLI exited 0):

```text
STATUS=complete
MODEL=phi4-mini:latest
MODEL_ANSWER_GROUNDED=False
TOOL_EXECUTIONS=1
TOOLS=investigate_incident
EVIDENCE_COMPLETE=True
AFFECTED_SERVICES=notification-service
PATHS=notification-service > checkout-api; notification-service > checkout-api > payment-service
DEPLOYMENTS=deploy-payment-2026-10-15, deploy-checkout-2026-10-12
ALERTS=alert-checkout-timeout-001, alert-payment-5xx-001
CAUSALITY=unproven
```

The model selected the real `investigate_incident` read tool and consumed FalkorDB results. Its free-text final response did not pass grounding validation, so the tested deterministic summary was returned. This is an observed safe fallback, not a claim that phi4-mini's free-text explanation passed.

## 4. MCP server

`incidentgraph mcp serve` runs a local stdio server using the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk). It publishes only the same four graph tools. The low-level SDK server publishes explicit schemas with `additionalProperties: false`; the server validates arguments again through the shared registry. It exposes no graph writes, arbitrary Cypher, ticketing, approval, shell, or arbitrary HTTP tools.

The provider follows [Ollama's OpenAI-compatible API documentation](https://ollama.com/blog/openai-compatibility) and constrains the endpoint to loopback.

The stdio integration test used an MCP Python client to discover the four tools and call `get_service_dependencies` against the live local FalkorDB 6.0.2 instance. It received five real `checkout-api` paths, confirmed an extra `cypher` argument fails with an MCP tool error, and confirmed an unknown service returns `not_found`. The test is `tests/test_mcp_integration.py`.

## 5. Validation evidence

Environment observed during validation:

- Python `3.13.9`; `uv 0.11.16`.
- FalkorDB image: `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0`; existing Compose container healthy and loopback-bound.
- Real integration fixtures verified the graph module and used the real FalkorDB service.
- MCP SDK `2.3.0`; HTTPX `0.28.1`; FalkorDB client `1.8.0`.
- Agentic Intent Gate `0.2.0`, installed source SHA `7261fdd2f47d2b15fabe8faac527eeee37338eb1`.
- Lock comparison found **no version changes to packages already present**; the lock only gained the direct MCP/HTTPX packages and their required transitive packages.

Commands and actual results:

```text
uv sync --locked --python 3.13
Resolved 47 packages; checked 46 packages

uv pip check --python .venv\Scripts\python.exe
All installed packages are compatible

uv run --locked --python 3.13 pytest -q tests/test_agent.py tests/test_graph_tools.py
29 passed in 0.18s

uv run --locked --env-file .env --python 3.13 pytest -q tests/test_graph_integration.py tests/test_mcp_integration.py
20 passed in 12.33s

uv run --locked --env-file .env --python 3.13 pytest -q
79 passed in 12.34s

uv run --locked --env-file .env --python 3.13 pytest -q tests/test_resultset_safety.py tests/test_mcp_integration.py tests/test_graph_integration.py tests/test_graph_tools.py tests/test_evidence_validation.py tests/test_agent.py
79 passed in 11.95s

ruff check src/incidentgraph_agent tests
All checks passed

black --check src/incidentgraph_agent/cli.py src/incidentgraph_agent/agent.py src/incidentgraph_agent/graph_tools.py src/incidentgraph_agent/mcp_server.py tests/test_agent.py tests/test_graph_tools.py tests/test_mcp_integration.py
7 files would be left unchanged

git diff --check
Passed (Git reported only its configured LF-to-CRLF working-copy notices)
```

The final pytest invocation lists test modules in a different order from default collection. Both full runs include the Phase 1.2 evidence-validation, result-set safety, graph integration, CLI regression, and isolated-graph tests.

## 6. Security boundaries

- The graph tool names and argument schemas are application-owned and fixed.
- All four tools are read-only and reuse the existing bounded repository methods.
- Ollama requests are restricted to a loopback endpoint, use no environment proxy, and have finite time and size limits.
- Model tool calls and final citations are validated; incomplete graph results or unsupported narratives cannot produce a successful grounded conclusion.
- The Agent does not invoke Agentic Intent Gate, register fake `SIDE_EFFECT` tools, approve actions, or execute remediation.
- No UI, external API, ticket integration, public database binding, or deployment was added.

## 7. Files changed

- `README.md`
- `pyproject.toml`, `uv.lock` — added required MCP SDK and HTTPX dependencies; existing locked package versions and the Runtime SHA were preserved.
- `src/incidentgraph_agent/agent.py` (new)
- `src/incidentgraph_agent/graph_tools.py` (new)
- `src/incidentgraph_agent/mcp_server.py` (new)
- `src/incidentgraph_agent/cli.py`
- `tests/test_agent.py` (new)
- `tests/test_graph_tools.py` (new)
- `tests/test_mcp_integration.py` (new)
- This report

`AGENTS.md`, the FalkorDB Compose/image configuration and volume, all Phase 0/1/1.1/1.2 reports, and the shared Agentic Intent Gate source were not modified.

## 8. Known limitations and Phase 3 readiness

- The graph is synthetic and contains no live telemetry, logs, traces, metrics, or incident ingestion. `evidence_complete` does not prove freshness, ground truth, or causality.
- The existing 500-row evidence cap still rejects larger result sets; pagination is not implemented.
- The live phi4-mini smoke test used the deterministic summary because its final free-text response failed grounding validation. Free-text explanation quality therefore remains limited; the real graph tool-selection and evidence path did pass.
- MCP is local stdio and the application remains a local single-user tool. No authentication product, web UI, or cloud deployment was added.
- Agentic Intent Gate v0.2.0 supports one trusted `SIDE_EFFECT` tool and single-step execution, not general read-only orchestration. Any later action integration needs a separate design/review that retains its approval and authorization gates.

**Phase 2 readiness: PASS — ready for independent review. Phase 3 readiness: NO-GO until that review and explicit user approval.** No commit, push, tag, or deployment was made.
