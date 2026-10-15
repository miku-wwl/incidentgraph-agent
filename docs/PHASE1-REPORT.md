# Graph Hacks — Phase 1 Evidence Report

- Verification date: 2026-10-15 NZDT
- Result: **PHASE 1: PASS — ready for user review; Phase 2 not started**
- Repository: incidentgraph-agent, branch main
- Scope: FalkorDB graph foundation and synthetic incident analysis. No commit, push, deployment, external API call, or remediation action was performed.

## 1. Competition compliance

Official pages checked on 2026-10-15:

- Rules: <https://www.wemakedevs.org/hackathons/falkordb/rules>
- Schedule: <https://www.wemakedevs.org/hackathons/falkordb/schedule>
- Event overview: <https://www.wemakedevs.org/hackathons/falkordb>

The hacking window is **2026-10-15 00:01 IST to 2026-10-18 23:59 IST**, equivalent to **2026-10-15 07:31 NZDT to 2026-10-19 07:29 NZDT**. The rules require main coding/design to begin after kickoff and the primary FalkorDB implementation to be new work. The Phase 1 application and graph implementation were created after the official kickoff. Pre-event preparation is preserved in commit cfcb35421b86b0351ccec30cefb83c9ee67e3574 and [docs/PHASE0-CLOSURE.md](PHASE0-CLOSURE.md). Agentic Intent Gate is a pre-existing external runtime dependency; its source was not copied or modified.

The rules require a public source repository, setup instructions and graph model, the Cypher/algorithms the product depends on, a demo video, deployment or complete local setup, track disclosure, and AI-assistant disclosure. README documents setup/model and Codex assistance. A demo video and final submission are outside this phase. Recheck the official pages before submission.

## 2. Architecture and graph model

    incidentgraph CLI
      └─ IncidentRepository: fixed, parameterized, bounded Cypher templates
           └─ FalkorStore: FalkorDB Python client
                └─ FalkorDB 6.0.2 in project-scoped Docker Compose

The seed command loads the deterministic synthetic fixture, ensures ID indexes and uniqueness constraints, then uses fixed MERGE templates. Query values are parameterized. Read operations use GRAPH.RO_QUERY. Input IDs and traversal depth are validated. Traversal is capped at four hops; path and evidence queries return at most 500 rows. A separate distinct-service count is returned for blast radius.

| Label | Count | Properties |
| --- | ---: | --- |
| Service | 8 | id, name, environment, criticality |
| Deployment | 2 | id, version, deployed_at, status |
| Alert | 2 | id, severity, timestamp, description |
| Incident | 1 | id, started_at, status, summary |
| Team | 4 | id, name |

| Relationship | Count | Direction |
| --- | ---: | --- |
| DEPENDS_ON | 7 | Service → Service; A requires B |
| DEPLOYED_TO | 2 | Deployment → Service |
| ALERTS_ON | 2 | Alert → Service |
| INVOLVES | 2 | Incident → Service |
| OWNED_BY | 8 | Service → Team |

Total: **17 nodes, 21 relationships**. FalkorDB reported operational exact-match ID indexes and unique node-ID constraints for Service, Deployment, Alert, Incident, and Team. The installed Python client exposes range-index creation, node unique-constraint creation, and index/constraint listing. Unique constraints need a supporting exact-match index and are asynchronous; the seed waits for operational state. Creating the first index also materializes a fresh graph key before calling db.constraints().

Actual dependency-path query:

    MATCH p=(source:Service {id: $service_id})-[:DEPENDS_ON*1..4]->(target:Service)
    WHERE length(p) <= $max_depth
    RETURN [n IN nodes(p) | n.id] AS path_ids, length(p) AS depth
    LIMIT $limit

Actual reverse blast-radius path query:

    MATCH p=(dependent:Service)-[:DEPENDS_ON*1..4]->(failed:Service {id: $service_id})
    WHERE length(p) <= $max_depth AND dependent.id <> failed.id
    RETURN dependent.id AS service_id,
           [n IN nodes(p) | n.id] AS path_ids,
           length(p) AS depth
    LIMIT $limit

A second fixed query with the same path filter returns count(DISTINCT dependent.id). Deployment/alert correlation uses fixed matches on (Deployment)-[:DEPLOYED_TO]->(Service) and (Alert)-[:ALERTS_ON]->(Service). All values are parameters; no caller provides Cypher.

## 3. Python and dependency evidence

Commands:

    uv sync --locked --python 3.13
    uv pip check --python .venv\Scripts\python.exe
    uv run --locked --python 3.13 python -   # inspected installed metadata and imports

Results:

- uv sync exited 0 and checked 34 locked packages.
- uv pip check: **All installed packages are compatible**.
- Python: **3.13.9**.
- agentic-intent-gate: **0.2.0**.
- direct_url.json commit: **7261fdd2f47d2b15fabe8faac527eeee37338eb1**.
- agentic_intent_gate.runtime imported from this project’s .venv\Lib\site-packages\agentic_intent_gate\runtime.py; verified inside the active environment and not from a source checkout.
- Public imports passed: ToolBinding, ToolCapability, ExecutableRegistry, CapabilityVerifier, BoundCapabilityAuthorization, execution_budget_binding, open_runtime.
- FalkorDB Python client: **1.8.0**, imported from this environment’s site-packages.
- pyproject.toml requires Python >=3.13,<3.14; uv.lock records the same immutable Git commit.

## 4. Docker and FalkorDB evidence

Commands and significant outputs:

    docker version
    Client=29.8.0 Server=29.8.0 OS=linux Arch=amd64

    docker context show
    desktop-linux

    docker compose ps
    incidentgraph-agent-phase1-falkordb-1  Up (healthy)
    127.0.0.1:16379->6379/tcp

    docker compose exec -T falkordb redis-cli PING
    PONG

    docker compose exec -T falkordb redis-cli MODULE LIST
    graph, version 60002
    vectorset, version 1

    docker compose exec -T falkordb redis-cli COMMAND INFO GRAPH.QUERY
    graph.QUERY command metadata returned

    uv run --locked --env-file .env --python 3.13 incidentgraph check
    {"graph_module":"loaded","graph_module_version":60002,
     "graph_query_command":"available","redis_ping":"PONG"}

Image: falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0, the Phase 0 verified 6.0.2 OCI index digest. The local engine is Linux/amd64; its image child manifest is sha256:e0b81c8e28c692f2857ea404dd6475dd6a861688660e0e529fc5acfb1ab1a558. The container uses ignored local .env authentication, binds only to loopback, and persists in the project-specific incidentgraph-agent-phase1-data volume. No password is included in this report. Startup logs had no ERROR, FATAL, or WARNING entries. The Compose service and volume remain available for review; unrelated Docker resources were not removed or changed.

## 5. CLI results against FalkorDB

Two consecutive real incidentgraph seed runs each returned **17 nodes and 21 relationships** with unchanged label counts.

incidentgraph dependencies checkout-api returned direct dependencies order-api and payment-service, with five sorted paths including both two-hop paths to shared payment-db and the path through ledger-service.

incidentgraph blast-radius payment-db returned **4 distinct affected services**:

| Service | Minimum depth | Path evidence |
| --- | ---: | --- |
| order-api | 1 | order-api → payment-db |
| payment-service | 1 | payment-service → payment-db |
| checkout-api | 2 | Two paths via order-api and payment-service |
| notification-service | 3 | Two paths through checkout-api |

catalog-api, search-api, and ledger-service were excluded from this blast radius.

incidentgraph correlate --incident incident-payment-001 returned the recent payment deployment, an older checkout deployment, and two alerts. It produced one timeline lead linking the recent payment deployment to a later payment alert. The lead and overall causality are marked **unproven**.

incidentgraph investigate incident-payment-001 returned one potentially impacted service, notification-service, through these actual graph paths:

    notification-service → checkout-api                         depth 1
    notification-service → checkout-api → payment-service      depth 2

The response includes deployment/alert evidence and says the synthetic graph has no logs, traces, or metric time series.

## 6. Integration tests and ablation

Command and final output:

    uv run --locked --env-file .env --python 3.13 pytest -q
    ...............                                                          [100%]
    15 passed in 2.40s

Fourteen tests exercised real FalkorDB 6.0.2: graph/module checks, counts, repeat-seed idempotency, unique ID constraints, dependency and reverse blast-radius traversal, shared paths, unaffected-service exclusion, depth limits, correlation without causal claims, incident paths, unknown IDs, invalid input, and bounded cycles. The fifteenth test is the controlled unavailable-database ablation described below. All 15 passed.

The ablation test reserves an ephemeral loopback port, releases it, and invokes the CLI against the unavailable local endpoint. Investigation exits with code 2 and JSON error code database_unavailable; it emits no potentially impacted services or other graph evidence. FalkorDB was not stopped.

A first fresh-graph run exposed that CALL db.constraints() fails before a graph key exists. Initialization was corrected to create and verify the required ID range index first, then create and poll each unique constraint. The final suite passed on fresh isolated test graphs and the existing seeded graph.

## 7. Limits and deferred work

- The dataset is synthetic; no live telemetry, logs, traces, metrics, or real incident ingestion is connected.
- Deployment/alert timing is correlation, not proof of causality or root cause.
- Traversal is capped at four hops and 500 rows per path/evidence query; the distinct blast-radius count is separate from its capped path list.
- The local CLI is single-user; authentication is local Redis password protection.
- Agent planning, MCP, UI, model provider, Runtime Action composition, approval/authorization, ticketing, remediation, cloud deployment, and final submission are not implemented.
- No performance benchmark was run; no measured speedup is claimed.
- The current upstream runtime dependency guide at <https://github.com/miku-wwl/agentic-intent-gate/blob/main/docs/AGENTS-dependence.md> agrees with the requested v0.2.0 commit. Its v0.1.0 mention describes backward compatibility for extended Actions, not an alternate dependency pin.

**Phase 2 readiness: NO-GO pending user review and explicit approval.** Phase 1 is complete for review. Phase 2 has not started.

## 8. Git and local state

No commit or push was made. README.md, Phase 1 source/tests/configuration, and this report are uncommitted local work. .env is ignored by Git. The Phase 1 Compose service and its project-owned volume remain available for review. No unrelated Docker resources were removed.
