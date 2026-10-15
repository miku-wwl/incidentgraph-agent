# Graph Hacks — Phase 1.1 Focused Hardening Report

- Verification date: 2026-10-15 NZDT
- Result: **PHASE 1.1: PASS — focused review findings closed; Phase 2 not started**
- Scope: bounded graph-evidence completeness and project-owned FalkorDB persistence only.
- Baseline: [Phase 1 evidence report](PHASE1-REPORT.md) remains unchanged.

## 1. Graph result completeness

The Phase 1 path queries used `LIMIT 500` without an order. This made the returned subset nondeterministic on larger graphs, and `len(rows) >= 500` incorrectly labeled exactly 500 complete rows as truncated. `investigate()` also consumed bounded evidence without a fail-closed completeness check.

The focused fix now:

- Fetches at most **501** path rows as a sentinel for the 500-row complete-evidence limit. Queries remain depth-bounded to 1–4 hops and use the existing finite FalkorDB query timeout.
- Returns complete sets up to and including 500 rows, sorted in application code by depth and service IDs. If the sentinel row is present, raises `incomplete_graph_evidence` and returns no partial path list. Thus the database may choose any 501 rows on an oversized graph, but that arbitrary prefix is never exposed as complete evidence.
- Filters repeated-node paths in Cypher before applying the row limit, preserving the existing cycle behavior without letting cyclic walks consume the result budget.
- Validates path shape, endpoint, and depth; malformed path evidence raises the same fail-closed error instead of being silently skipped.
- Applies the same sentinel rule to bounded deployment and alert evidence used by investigations. `investigate()` checks completeness flags before combining evidence.
- Marks successful dependency, blast-radius, correlation, and investigation results with `evidence_complete: true`; their existing truncation fields are false on successful returns. An oversized or invalid result raises before a result dictionary is returned.
- Derives the distinct affected-service count from the complete bounded path set. This removes the previous separate variable-length path-count query, which could enumerate an unbounded number of paths.

The 500-row boundary tests use real FalkorDB:

- Exactly **500** dependency paths return all rows and produce the same sorted order on consecutive calls.
- Exactly **501** dependency paths raise `IncompleteGraphEvidence` with code `incomplete_graph_evidence` and no partial evidence.
- A 501-path reverse blast radius raises the same error; `investigate()` propagates it without producing an incomplete investigation result.
- The existing bounded-cycle test still passes.

## 2. FalkorDB persistence

The project service was `incidentgraph-agent-phase1-falkordb-1`, using the pinned Phase 0 image digest and the project volume `incidentgraph-agent-phase1-data` mounted at `/var/lib/falkordb/data`. No other Docker service or volume was changed or removed.

Before this check, live Redis configuration reported `appendonly=no`, `appendfsync=everysec`, and `dir=/var/lib/falkordb/data`; the mounted directory contained no persistence files. The existing synthetic graph baseline was 17 nodes and 21 relationships.

To protect the existing graph while enabling AOF:

1. Ran `docker compose exec -T falkordb redis-cli BGSAVE`; waited for `rdb_bgsave_in_progress:0` and `rdb_last_bgsave_status:ok`. This produced `dump.rdb` in the project volume.
2. Enabled AOF on the live server with `docker compose exec -T falkordb redis-cli CONFIG SET appendonly yes`; waited for rewrite completion and `aof_last_bgrewrite_status:ok` / `aof_last_write_status:ok`.
3. Updated only this service's `REDIS_ARGS` in `compose.yaml` to include `--appendonly yes --appendfsync everysec`, then ran `docker compose up -d --no-deps falkordb` to apply it.
4. Ran `docker compose restart falkordb` and verified the same container ID returned healthy, retained the same named volume mount, and loaded the graph again.

After restart, `CONFIG GET` reported:

```text
appendonly yes
appendfsync everysec
dir /var/lib/falkordb/data
appenddirname appendonlydir
```

`INFO persistence` reported `loading:0`, `aof_enabled:1`, no rewrite in progress or scheduled, and successful AOF rewrite/write status. The AOF manifest and base/incremental files were present at `/var/lib/falkordb/data/appendonlydir` inside the mounted project volume.

The post-restart application/Cypher checks matched the pre-restart graph:

- Node counts: Service 8, Deployment 2, Alert 2, Incident 1, Team 4 (**17 total**).
- Relationship count: **21**.
- `checkout-api` dependency query: the same five direct and multi-hop paths.
- `payment-db` blast radius: `order-api`, `payment-service`, `checkout-api`, `notification-service` (**4 distinct services**).
- `incident-payment-001` investigation: `notification-service` remains the only potentially impacted service.

The Compose configuration follows FalkorDB's [Docker persistence guidance](https://falkordb.github.io/docs-staging/operations/docker/) for `REDIS_ARGS`, AOF, and named volumes. Redis documents `appendfsync everysec` as the usual balance between write cost and durability; see [Redis persistence](https://redis.io/docs/latest/management/persistence/).

## 3. Validation

Commands and results:

```text
uv sync --locked --python 3.13
Resolved 34 packages; checked 34 packages

uv pip check --python .venv\Scripts\python.exe
All installed packages are compatible

uv run --locked --env-file .env --python 3.13 pytest -q
18 passed in 3.71s
```

The full suite used the live FalkorDB 6.0.2 service. The three new integration cases cover exact-500 ordering/completeness, 501-path dependency rejection, and fail-closed blast-radius/investigation behavior.

Runtime verification after sync:

- Python: 3.13.9.
- Installed `agentic-intent-gate`: 0.2.0.
- `direct_url.json` commit: `7261fdd2f47d2b15fabe8faac527eeee37338eb1`.
- Public `agentic_intent_gate.runtime` imports passed from the project `.venv`.
- `uv.lock` and the pinned Runtime dependency were not changed.

## 4. Changed files and remaining risks

Phase 1.1 changes are limited to:

- `compose.yaml`
- `src/incidentgraph_agent/errors.py`
- `src/incidentgraph_agent/repository.py`
- `tests/test_graph_integration.py`
- This report

Phase 0 and Phase 1 reports were not edited. No Agent, MCP, UI, or Phase 2 work was started. No commit or push was made for Phase 1.1.

Remaining risks:

- AOF with `appendfsync everysec` can lose recent writes if the host fails between syncs; this is a local single-node development setup, not HA or a backup strategy.
- The restart test verifies this container and volume on the current host; it does not prove recovery after host/disk loss.
- `evidence_complete` means the bounded queries returned and validated all rows within their caps. It does not establish source freshness, data correctness, causality, or authorization to execute an external action. No Agent or action authorization flow exists in this phase.
- A graph with more than 500 matching paths/evidence rows now fails closed and needs a narrower query or a separately reviewed pagination design.

**Phase 1.1 status: PASS. Phase 2 remains not started.**
