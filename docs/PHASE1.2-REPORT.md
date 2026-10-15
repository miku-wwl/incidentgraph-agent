# Graph Hacks — Phase 1.2 Final Corrections Report

- Verification date: 2026-10-15 NZDT
- Baseline: `19228f584e8d560bb7dbf5f53dc0d7fa15e5ef92`
- Result: **PHASE 1.2: PASS — five review findings addressed; Phase 2 not started**
- Scope: isolated graph tests, evidence validation, README accuracy, timezone ordering, and FalkorDB result-set safety.

## 1. Review findings and fixes

1. **Test isolation:** `store` and `repository` fixtures are function-scoped. Each test that needs a graph receives a unique UUID-based graph name, seeds its own graph, and deletes only that graph during teardown. All prior assertions remain in place.
2. **Evidence validation:** Added shared schema validation for required nonempty string fields, identifiers, and timezone-aware ISO 8601 timestamps on Service, Deployment, Alert, Incident, and Team dataset records. Repository query results are validated before use or serialization. Missing values are never stringified as `"None"`; malformed graph evidence raises `incomplete_graph_evidence`.
3. **README consistency:** Updated path examples to include current node-identity cycle filtering, removed the obsolete separate blast-radius count query, documented the 501-row sentinel and failure codes, and linked the Phase 1.1 report. The Phase 0 and Phase 1 evidence reports were preserved.
4. **Timezone ordering:** Deployment and alert results are parsed as timezone-aware datetimes and sorted by actual instant, with stable ascending ID tie-breakers. The original timestamp strings remain unchanged in output.
5. **FalkorDB result-set safety:** Before and after every read query, the store reads `GRAPH.CONFIG GET RESULTSET_SIZE`. Negative values are treated as unlimited; nonnegative values below 501, malformed responses, and read failures stop the operation before evidence can be returned. The application does not alter the global setting. The repeated check detects runtime changes that remain in effect during a query and blocks subsequent queries after a lower limit is set.

## 2. Files changed

- `README.md`
- `src/incidentgraph_agent/database.py`
- `src/incidentgraph_agent/dataset.py`
- `src/incidentgraph_agent/evidence.py` (new)
- `src/incidentgraph_agent/repository.py`
- `tests/conftest.py`
- `tests/test_graph_integration.py`
- `tests/test_evidence_validation.py` (new)
- `tests/test_resultset_safety.py` (new)
- This report

`AGENTS.md`, `pyproject.toml`, `uv.lock`, `compose.yaml`, the FalkorDB image digest, Phase 0/Phase 1 reports, and the shared Agentic Intent Gate source were not changed.

## 3. Validation results

Commands and actual results:

```text
uv sync --locked --python 3.13
Resolved 34 packages; checked 34 packages

uv pip check --python .venv\Scripts\python.exe
All installed packages are compatible

uv run --locked --python 3.13 pytest -q tests/test_evidence_validation.py tests/test_resultset_safety.py
30 passed in 0.10s

uv run --locked --env-file .env --python 3.13 pytest -q
49 passed in 6.73s

uv run --locked --env-file .env --python 3.13 pytest -q -p reverse_pytest_order
49 passed in 6.90s

ruff check src/incidentgraph_agent/evidence.py src/incidentgraph_agent/database.py src/incidentgraph_agent/dataset.py src/incidentgraph_agent/repository.py tests/conftest.py tests/test_graph_integration.py tests/test_evidence_validation.py tests/test_resultset_safety.py
All checks passed

black --check src/incidentgraph_agent/evidence.py src/incidentgraph_agent/database.py src/incidentgraph_agent/dataset.py src/incidentgraph_agent/repository.py tests/conftest.py tests/test_graph_integration.py tests/test_evidence_validation.py tests/test_resultset_safety.py
8 files would be left unchanged

git diff --check
Passed
```

The second full-suite run used a temporary pytest plugin that reversed the collected test items; the plugin was removed afterward. Both complete runs used real FalkorDB integration tests. Redis reported graph module version `60002` (FalkorDB 6.0.2), and `GRAPH.CONFIG GET RESULTSET_SIZE` returned `10000`. The container remained healthy on the pinned image `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0`. No server configuration was changed.

Installed runtime verification:

- `agentic-intent-gate`: `0.2.0`
- Installed source commit: `7261fdd2f47d2b15fabe8faac527eeee37338eb1`
- Imported from the project `.venv`; lockfile remains pinned to the same commit.

## 4. Regression evidence

- Both full-suite orders passed with 49 tests. The cycle test's graph additions did not affect the fixed seed-count assertions in other tests. A Redis scan after the runs found no `incidentgraph_test_*` graph; `GRAPH.LIST` showed only the project graph `incidentgraph_phase1`.
- Negative tests cover missing required properties across all five node labels, invalid property types and identifiers, empty strings, naive and malformed timestamps, malformed query rows, too-small result-set caps, invalid configuration responses, configuration-read failures, and a result-set limit lowered during a query or before the next query.
- Real FalkorDB mixed-offset tests confirm deployments and alerts sort by actual instant while returning their original timestamp strings.
- Real CLI regression outputs remained: five `checkout-api` dependency paths; four distinct services in the `payment-db` blast radius; one potentially impacted service (`notification-service`) in the incident investigation. Complete-evidence flags remained true for these results.
- Real server configuration remained `RESULTSET_SIZE=10000`; tests and application code did not change it.

## 5. Remaining risks

- The 500-row evidence cap still rejects larger result sets; pagination is not implemented.
- The server setting is global. Checks run immediately before and after each query, but a privileged concurrent operator who lowers and restores the limit entirely between those checks could evade observation.
- Each read now performs two additional configuration reads; no latency benchmark was run.
- The graph and evidence remain synthetic. Temporal ordering does not establish causation or source freshness.
- FalkorDB remains a single local instance. The Phase 1.1 AOF configuration uses `appendfsync everysec`, so a host failure can lose recent writes; AOF is not a backup or high-availability plan.
- Agent planning, MCP, UI, and action authorization/remediation remain unimplemented.

## 6. Phase 2 readiness

**Phase 1.2: PASS — ready for review. Phase 2 has not started and remains pending explicit user approval.**
