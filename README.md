# incidentgraph-agent

> **Graph Hacks 2026 — Phase 1 foundation implemented after kickoff.** Phase 0 was pre-competition preparation. Phase 1 adds a local FalkorDB graph, deterministic synthetic incident evidence, bounded Cypher analysis, and a JSON CLI. Phase 2 is not started.

## Competition and project

[Graph Hacks: Context for AI Agents — official rules](https://www.wemakedevs.org/hackathons/falkordb/rules) · [schedule](https://www.wemakedevs.org/hackathons/falkordb/schedule) · [event overview](https://www.wemakedevs.org/hackathons/falkordb)

**Track:** Agents That Act on Connected Data

**Concept:** Graph-powered incident investigation and safe remediation. FalkorDB is the primary graph database and performs dependency traversal, impact analysis, and incident evidence correlation.

The official hacking window is **October 15, 2026, 12:01 AM IST (7:31 AM NZDT)** through **October 18, 2026, 11:59 PM IST (October 19, 7:29 AM NZDT)**. The official rules and event overview require the main work to start after kickoff and the primary graph implementation to be new work. Check the official pages for updates before submission.

## Phase status and provenance

- **Phase 0, completed before kickoff:** AGENTS.md and README preparation, Docker/FalkorDB infrastructure verification, Agentic Intent Gate dependency verification, and the [Phase 0 evidence report](docs/PHASE0-CLOSURE.md). No competition-specific application implementation was done before kickoff.
- **Phase 1, implemented after kickoff:** the consumer Python package, FalkorDB Compose configuration, synthetic graph, seed command, read-only investigation queries, tests, and this documentation.
- **Pre-existing shared runtime:** [Agentic Intent Gate](https://github.com/miku-wwl/agentic-intent-gate), consumed as an installed dependency. Its source was not copied or modified.
- **Phase 2 and later, planned:** Agent/model planning, MCP, UI, and any approved action integration. These are not implemented.

Codex assisted with implementation and documentation. The project owner is responsible for reviewing and being able to explain the submitted code and design.

## Architecture and technology

Python 3.13 CLI → small repository/query layer → FalkorDB Python client → local FalkorDB 6.0.2. Graph reads use predefined parameterized Cypher; no model or user input can provide Cypher. Dependency traversal is bounded to four hops. Path and evidence queries fetch up to 501 rows as a sentinel and return at most 500 rows only when completeness is established. Before and after each graph read, the application verifies that FalkorDB's runtime-mutable `RESULTSET_SIZE` is unlimited or at least 501. The runtime dependency is pinned in `pyproject.toml` and `uv.lock` to commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1` (`agentic-intent-gate==0.2.0`). This phase verifies and installs the runtime but does not compose or execute Runtime Actions.

### Graph model

All records are deterministic synthetic data in `src/incidentgraph_agent/synthetic_incident.json`.

| Node | Properties |
| --- | --- |
| `Service` | `id`, `name`, `environment`, `criticality` |
| `Deployment` | `id`, `version`, `deployed_at`, `status` |
| `Alert` | `id`, `severity`, `timestamp`, `description` |
| `Incident` | `id`, `started_at`, `status`, `summary` |
| `Team` | `id`, `name` |

| Relationship | Direction and meaning |
| --- | --- |
| `(:Service)-[:DEPENDS_ON]->(:Service)` | A requires B. Blast radius follows incoming edges. |
| `(:Deployment)-[:DEPLOYED_TO]->(:Service)` | Deployment applies to service. |
| `(:Alert)-[:ALERTS_ON]->(:Service)` | Alert concerns service. |
| `(:Incident)-[:INVOLVES]->(:Service)` | Incident includes service. |
| `(:Service)-[:OWNED_BY]->(:Team)` | Team owns service. |

The seeded graph has 8 services, 2 deployments, 2 alerts, 1 incident, 4 teams, and 21 relationships. FalkorDB 6.0.2 creates an exact-match `id` index and an operational unique `id` constraint for each node label. These keep stable identities enforceable and support the ID lookups; no performance claim is made.

## Local setup

Prerequisites: Windows, Python 3.13, `uv`, Docker Desktop with its Linux Engine, Docker Compose, and GitHub/container-registry access for dependency and image downloads.

1. Copy `.env.example` to `.env` and replace `FALKORDB_PASSWORD` with a random 32-byte Base64 password. `.env` is ignored by Git; do not commit it.
2. Install the locked dependencies and start the local database:

   ```powershell
   uv sync --locked --python 3.13
   docker compose up -d --wait
   uv run --locked --env-file .env --python 3.13 incidentgraph check
   ```

3. Seed and inspect the synthetic graph:

   ```powershell
   uv run --locked --env-file .env --python 3.13 incidentgraph seed
   uv run --locked --env-file .env --python 3.13 incidentgraph dependencies checkout-api
   uv run --locked --env-file .env --python 3.13 incidentgraph blast-radius payment-db
   uv run --locked --env-file .env --python 3.13 incidentgraph correlate --incident incident-payment-001
   uv run --locked --env-file .env --python 3.13 incidentgraph investigate incident-payment-001
   ```

Compose uses `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0` (Phase 0 verified tag `6.0.2`, Linux/amd64 child manifest `sha256:e0b81c8e28c692f2857ea404dd6475dd6a861688660e0e529fc5acfb1ab1a558`). The service binds only to `127.0.0.1:16379` and persists to the project-specific `incidentgraph-agent-phase1-data` volume. Keep the local password private.

## Queries and example results

`DEPENDS_ON` means the source requires the target. The dependency query returns paths in that direction:

```cypher
MATCH p=(source:Service {id: $service_id})-[:DEPENDS_ON*1..4]->(target:Service)
WHERE length(p) <= $max_depth
  AND all(node IN nodes(p) WHERE single(candidate IN nodes(p) WHERE id(candidate) = id(node)))
RETURN [n IN nodes(p) | n.id] AS path_ids, length(p) AS depth
LIMIT $limit
```

Blast radius traverses the incoming dependency direction, from affected service to failed service:

```cypher
MATCH p=(dependent:Service)-[:DEPENDS_ON*1..4]->(failed:Service {id: $service_id})
WHERE length(p) <= $max_depth AND dependent.id <> failed.id
  AND all(node IN nodes(p) WHERE single(candidate IN nodes(p) WHERE id(candidate) = id(node)))
RETURN dependent.id AS service_id,
       [n IN nodes(p) | n.id] AS path_ids,
       length(p) AS depth
LIMIT $limit
```

The repository binds `$limit` to 501 for path, deployment, and alert queries. It accepts a result only when at most 500 rows are returned, validates each required field, and sorts paths deterministically. A 501st row proves the evidence exceeds the supported result cap; the application raises `incomplete_graph_evidence` and returns no partial result. The distinct affected-service count is derived from the complete path set, so there is no separate count query.

`incomplete_graph_evidence` is also raised when path or evidence fields are missing or invalid, a path cannot be validated, or FalkorDB's `RESULTSET_SIZE` is below 501. If the setting cannot be read or has an invalid response, the read fails with `database_unavailable` or `graph_query_failed`. Since this FalkorDB setting can change at runtime, it is checked immediately before and after every graph read. The application does not change the server's global setting.

Incident evidence is retrieved with these fixed relationship patterns:

```cypher
MATCH (d:Deployment)-[:DEPLOYED_TO]->(s:Service)
WHERE s.id IN $service_ids
RETURN DISTINCT d.id AS id,
       d.version AS version,
       d.deployed_at AS deployed_at,
       d.status AS status,
       s.id AS service_id
ORDER BY d.deployed_at DESC, d.id, s.id
LIMIT $limit
```

```cypher
MATCH (a:Alert)-[:ALERTS_ON]->(s:Service)
WHERE s.id IN $service_ids
RETURN DISTINCT a.id AS id,
       a.severity AS severity,
       a.timestamp AS timestamp,
       a.description AS description,
       s.id AS service_id
ORDER BY a.timestamp DESC, a.id, s.id
LIMIT $limit
```

Values are bound as parameters, and templates are fixed in `repository.py`.

Verified example: `incidentgraph blast-radius payment-db` returns four distinct affected services: `order-api` and `payment-service` at depth 1, `checkout-api` at depth 2 by two paths, and `notification-service` at depth 3 by two paths. `catalog-api`, `search-api`, and `ledger-service` are excluded from this blast radius.

Verified incident output: `incidentgraph investigate incident-payment-001` finds `notification-service` potentially impacted through `notification-service → checkout-api` (depth 1) and `notification-service → checkout-api → payment-service` (depth 2). It returns the recent payment deployment and two synthetic alerts. The recent deployment is only a lead: `causality.status` is `unproven` because the dataset has no logs, traces, or metric time series.

## Tests and limits

Run real FalkorDB integration tests with:

```powershell
uv run --locked --env-file .env --python 3.13 pytest -q
```

The Phase 1 evidence report records the original implementation evidence: [docs/PHASE1-REPORT.md](docs/PHASE1-REPORT.md). The focused hardening evidence is in [docs/PHASE1.1-REPORT.md](docs/PHASE1.1-REPORT.md).

Current limits: synthetic single-incident dataset; no live telemetry or incident ingestion; causality is not inferred; traversal depth is at most four and each path query returns at most 500 rows; local single-user setup. No Agent, MCP server, UI, model orchestration, approval flow, remediation action, external API call, or deployment was implemented in Phase 1.
