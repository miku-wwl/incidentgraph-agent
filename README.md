# incidentgraph-agent

> **Graph Hacks 2026 — Phase 2 Agent and MCP implementation.** Phase 0 was pre-competition preparation. Phase 1 added the local FalkorDB graph and deterministic investigation. Phase 2 adds a bounded local Agent Loop, trusted read-only graph tools, and an stdio MCP server.

## Competition and project

[Graph Hacks: Context for AI Agents — official rules](https://www.wemakedevs.org/hackathons/falkordb/rules) · [schedule](https://www.wemakedevs.org/hackathons/falkordb/schedule) · [event overview](https://www.wemakedevs.org/hackathons/falkordb)

**Track:** Agents That Act on Connected Data

**Concept:** Graph-powered incident investigation and safe remediation. FalkorDB is the primary graph database and performs dependency traversal, impact analysis, and incident evidence correlation.

The official hacking window is **October 15, 2026, 12:01 AM IST (7:31 AM NZDT)** through **October 18, 2026, 11:59 PM IST (October 19, 7:29 AM NZDT)**. The official rules and event overview require the main work to start after kickoff and the primary graph implementation to be new work. Check the official pages for updates before submission.

## Phase status and provenance

- **Phase 0, completed before kickoff:** AGENTS.md and README preparation, Docker/FalkorDB infrastructure verification, Agentic Intent Gate dependency verification, and the [Phase 0 evidence report](docs/PHASE0-CLOSURE.md). No competition-specific application implementation was done before kickoff.
- **Phase 1, implemented after kickoff:** the consumer Python package, FalkorDB Compose configuration, synthetic graph, seed command, read-only investigation queries, tests, and this documentation.
- **Pre-existing shared runtime:** [Agentic Intent Gate](https://github.com/miku-wwl/agentic-intent-gate) v0.2.0 remains pinned to `7261fdd2f47d2b15fabe8faac527eeee37338eb1` as an installed dependency. Its source was not copied or modified. Phase 2 does not compose or execute Runtime Actions; the dependency is retained for a separately reviewed Phase 3 integration.
- **Phase 2, implemented after kickoff:** local Ollama model decisions, the bounded Agent Loop, four trusted graph tools, the stdio MCP server, CLI entry points, and their tests.
- **Phase 3, planned:** any Runtime Action composition, approval/authorization flow, or remediation. Nothing in Phase 2 executes side effects.

Codex assisted with implementation and documentation. The project owner is responsible for reviewing and being able to explain the submitted code and design.

## Architecture and technology

Python 3.13 CLI → small repository/query layer → FalkorDB Python client → local FalkorDB 6.0.2. Graph reads use predefined parameterized Cypher; no model or user input can provide Cypher. Dependency traversal is bounded to four hops. Path and evidence queries fetch up to 501 rows as a sentinel and return at most 500 rows only when completeness is established. Before and after each graph read, the application verifies that FalkorDB's runtime-mutable `RESULTSET_SIZE` is unlimited or at least 501. The runtime dependency is pinned in `pyproject.toml` and `uv.lock` to commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1` (`agentic-intent-gate==0.2.0`). This phase verifies and installs the runtime but does not compose or execute Runtime Actions.

### Phase 2 Agent and trusted graph tools

`incidentgraph agent ask "..."` uses a small decision loop with a maximum of four graph-tool executions per request. The tool allowlist and schemas are fixed in application code. Every model decision is parsed as bounded strict JSON; unknown tools, extra arguments, invalid identifiers, and depth outside 1–4 are rejected. The model cannot provide Cypher, URLs, credentials, shell commands, database configuration, or tool implementations.

The local Ollama provider calls only a loopback OpenAI-compatible `/v1` endpoint, verifies the configured model is installed, disables environment proxy use, and applies a finite request timeout and output/context limits. Defaults are `http://127.0.0.1:11434/v1`, `phi4-mini:latest`, and 45 seconds. Override `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, or `OLLAMA_TIMEOUT_SECONDS` only with local values; remote endpoints are rejected. There is no cloud fallback or automatic model download.

The four read-only tools map directly to `IncidentRepository`: `get_service_dependencies`, `calculate_blast_radius`, `correlate_incident_evidence`, and `investigate_incident`. They return real repository results, `evidence_complete`, and stable references such as `incident:incident-payment-001`, `deployment:deploy-payment-2026-10-15`, and `path:notification-service>checkout-api`. Errors remain errors; incomplete results cannot become successful Agent answers. A final model explanation is checked against observed entity IDs, paths, citations, and causal wording. If it fails validation, the Agent uses a deterministic summary generated from the structured graph results.

Every completed incident response contains the observed tool trace and graph results, evidence references, relevant deployment/alert records, dependency paths, uncertainty, and `causality.status: unproven`. `evidence_complete=true` covers query completeness and field validation only; it does not establish source freshness, ground truth, causality, or remediation authorization.

The MCP server uses the official Python MCP SDK's low-level server over local stdio. `incidentgraph mcp serve` exposes the same four tools with explicit JSON Schemas (`additionalProperties: false`) and server-side argument validation. It has no graph-write, arbitrary Cypher, ticket, approval, shell, or arbitrary HTTP tool.

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
   uv run --locked --env-file .env --python 3.13 incidentgraph agent ask "Investigate incident-payment-001 and explain which services may be affected."
   ```

4. To connect an MCP client, configure the local command `uv run --locked --env-file .env --python 3.13 incidentgraph mcp serve` with stdio transport. Keep `.env` local; the MCP protocol output is written to stdout and startup errors go to stderr.

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

The unit suite for the tool registry and Agent loop can be run with `uv run --locked --python 3.13 pytest -q tests/test_graph_tools.py tests/test_agent.py`. `tests/test_mcp_integration.py` starts the MCP server over stdio and uses a real MCP client to query the real FalkorDB service.

Historical evidence: [Phase 0 closure](docs/PHASE0-CLOSURE.md), [Phase 1 report](docs/PHASE1-REPORT.md), [Phase 1.1 report](docs/PHASE1.1-REPORT.md), [Phase 1.2 report](docs/PHASE1.2-REPORT.md). Phase 2 implementation and execution evidence is recorded in [docs/PHASE2-REPORT.md](docs/PHASE2-REPORT.md).

Current limits: synthetic single-incident dataset; no live telemetry or incident ingestion; temporal correlation does not infer causality; traversal depth is at most four and each path query returns at most 500 rows; local single-user setup. There is no web UI, cloud deployment, ticket integration, or Phase 3 action execution. The graph tools are read-only; no action is authorized or executed by this Agent.
