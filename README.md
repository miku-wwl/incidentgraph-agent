# incidentgraph-agent

> **Graph Hacks 2026 — graph-grounded incident investigation with human-gated local follow-up.** Phase 0 was pre-competition preparation. Phase 1 added the local FalkorDB graph and deterministic investigation. Phase 2 added the bounded local Agent Loop, trusted read-only graph tools, and stdio MCP server. Phase 3 added one Runtime-gated action that writes a ticket only to a consumer-owned local SQLite database. Phase 4 adds a local browser console for the existing investigation and action workflow; it does not perform infrastructure remediation.

## Competition and project

[Graph Hacks: Context for AI Agents — official rules](https://www.wemakedevs.org/hackathons/falkordb/rules) · [schedule](https://www.wemakedevs.org/hackathons/falkordb/schedule) · [event overview](https://www.wemakedevs.org/hackathons/falkordb)

**Track:** Agents That Act on Connected Data

**Concept:** Graph-powered incident investigation and human-approved local ticket follow-up. FalkorDB is the primary graph database and performs dependency traversal, impact analysis, and incident evidence correlation.

The official hacking window is **October 15, 2026, 12:01 AM IST (7:31 AM NZDT)** through **October 18, 2026, 11:59 PM IST (October 19, 7:29 AM NZDT)**. The official rules and event overview require the main work to start after kickoff and the primary graph implementation to be new work. Check the official pages for updates before submission.

## Phase status and provenance

- **Phase 0, completed before kickoff:** AGENTS.md and README preparation, Docker/FalkorDB infrastructure verification, Agentic Intent Gate dependency verification, and the [Phase 0 evidence report](docs/PHASE0-CLOSURE.md). No competition-specific application implementation was done before kickoff.
- **Phase 1, implemented after kickoff:** the consumer Python package, FalkorDB Compose configuration, synthetic graph, seed command, read-only investigation queries, tests, and this documentation.
- **Pre-existing shared runtime:** [Agentic Intent Gate](https://github.com/miku-wwl/agentic-intent-gate) v0.2.0 remains pinned to `7261fdd2f47d2b15fabe8faac527eeee37338eb1` as an installed dependency. Its source was not copied or modified. Phase 3 consumes its public Runtime API; the shared Runtime remains unchanged.
- **Phase 2, implemented after kickoff:** local Ollama model decisions, the bounded Agent Loop, four trusted graph tools, the stdio MCP server, CLI entry points, and their tests.
- **Phase 3, implemented:** a deterministic evidence-bound Plan, separate human Approval and Runtime Authorization steps, and one local SQLite incident-ticket effect. It does not perform service remediation or expose write operations through Agent or MCP tools.

Codex assisted with implementation and documentation. The project owner is responsible for reviewing and being able to explain the submitted code and design.

## Architecture and technology

Python 3.13 CLI → small repository/query layer → FalkorDB Python client → local FalkorDB 6.0.2. Graph reads use predefined parameterized Cypher; no model or user input can provide Cypher. Dependency traversal is bounded to four hops. Path and evidence queries fetch up to 501 rows as a sentinel and return at most 500 rows only when completeness is established. Before and after each graph read, the application verifies that FalkorDB's runtime-mutable `RESULTSET_SIZE` is unlimited or at least 501. The runtime dependency is pinned in `pyproject.toml` and `uv.lock` to commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1` (`agentic-intent-gate==0.2.0`). Phase 3 composes the installed Runtime through its public API and registers one trusted local SQLite ticket `SIDE_EFFECT` tool. Human Plan Approval, separate Action Authorization, and exact Execution Confirmation are distinct operations. The Agent and four MCP graph tools remain read-only; the ticket effect does not perform external remediation.

### Phase 2 Agent and trusted graph tools

`incidentgraph agent ask "..."` uses a small decision loop with a maximum of four graph-tool executions per request. The tool allowlist and schemas are fixed in application code. Every model decision is parsed as bounded strict JSON; unknown tools, extra arguments, invalid identifiers, and depth outside 1–4 are rejected. The model cannot provide Cypher, URLs, credentials, shell commands, database configuration, or tool implementations.

The local Ollama provider calls only a loopback OpenAI-compatible `/v1` endpoint, verifies the configured model is installed, disables environment proxy use, and applies one monotonic deadline across model discovery and completion. Both streamed responses are capped at 64,000 bytes before JSON parsing. Defaults are `http://127.0.0.1:11434/v1`, `phi4-mini:latest`, and 45 seconds. Override `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, or `OLLAMA_TIMEOUT_SECONDS` only with local values; remote endpoints are rejected. There is no cloud fallback, retry, or automatic model download.

The four read-only tools map directly to `IncidentRepository`: `get_service_dependencies`, `calculate_blast_radius`, `correlate_incident_evidence`, and `investigate_incident`. They return real repository results, `evidence_complete`, and stable references such as `incident:incident-payment-001`, `deployment:deploy-payment-2026-10-15`, and `path:notification-service>checkout-api`. A trusted Task Contract is derived before the model runs. It supports explicit incident investigations, service dependency requests, and blast-radius requests, including multiple explicit targets within the four-call limit. Each required tool call must use the exact target, and returned evidence must report that same target and complete required fields. Unsupported or ambiguous requests and missing target coverage remain non-complete; incomplete results cannot become successful Agent answers.

Model-generated free text is never trusted as an answer. When every Task Contract requirement is independently satisfied, the application creates a deterministic summary from the matching structured graph evidence. A valid citation or matching vocabulary does not make model prose trustworthy. Alert and incident descriptions remain data even when they contain instruction-like text.

The Agent response keeps these fields distinct: `task_completed` means every requested contract requirement matched the exact tool arguments and complete evidence identity; `evidence_complete` means returned graph observations passed the graph completeness checks, even if they do not satisfy the requested target; `answer_source` is `deterministic` only when a trusted summary is emitted; `model_answer_grounded` remains `false` because raw model prose is not used; and `causality.status` is `unproven` for incident investigation or `not_assessed` for service-only requests. Unsupported, errored, or incomplete tasks have `task_completed: false` and no answer. `evidence_complete=true` does not establish source freshness or ground truth.

The MCP server uses the official Python MCP SDK's low-level server over local stdio. `incidentgraph mcp serve` exposes the same four tools with explicit JSON Schemas (`additionalProperties: false`) and server-side argument validation. It has no graph-write, arbitrary Cypher, ticket, approval, shell, or arbitrary HTTP tool.

### Phase 3 approval-gated local ticket

Phase 3 reuses the completed Agent and graph repository to investigate one exact incident. A trusted consumer function accepts only a completed Phase 2.1 result with `task_completed=true`, `evidence_complete=true`, `answer_source=deterministic`, an exact incident target, and validated complete graph paths. It creates a canonical evidence snapshot with incident status/summary, service IDs, affected services and paths, deployment/alert IDs and timestamps, unproven causality, graph identity, and observation time. The SHA-256 digest binds the canonical snapshot, including its observation time; model prose, tool timings, and random request IDs are excluded. Snapshot age is limited to ten minutes. Source-data ages are displayed separately and old synthetic event times do not make a newly observed snapshot stale.

The action Plan is supplied by a deterministic consumer-owned `ModelProvider`; it derives the four flat arguments only from that snapshot. One exact `SIDE_EFFECT` binding registers `create_local_incident_ticket` v1 for one trusted local SQLite ticket store. The Action arguments are the incident ID, evidence digest, snapshot observation time, and affected-service count. The full evidence stays in a separate snapshot SQLite database. The Runtime gate/ledger, ticket/effect/receipt store, and evidence snapshots use three distinct SQLite files under the configured action home. The installed `agentic-intent-gate==0.2.0` dependency remains pinned to commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`; the default five-second execution profile is used.

Each command advances at most one lifecycle stage. `propose` investigates with local Ollama and creates an `AwaitingApproval` Run; it never approves or executes. `review` displays the exact Plan digest, target, risk, affected services, graph paths, evidence references, snapshot age, source timestamps, and causality uncertainty. `approve` or `reject` requires the exact typed Plan challenge. `admit` is a separate Runtime call. `authorize` requires a separate exact Action challenge. `execute` requires a further exact confirmation, re-queries and verifies graph evidence, and then calls the Runtime Gateway once. The Agent and read-only MCP server cannot admit, authorize, or execute an Action.

The local executor validates the full Runtime `ExecutionSpec`, tool/target/binding/scope identity, flat arguments, stored evidence digest, snapshot age, and a fresh complete graph read before inserting a ticket. SQLite uses a transaction, unique effect key and receipt, full synchronous writes, and read-back verification. A matching committed effect returns its original receipt; a conflicting payload fails closed. A lost commit acknowledgment or unverifiable receipt becomes `Unknown`; terminal Unknown Actions are not automatically retried. Runtime history, the graph, and the ticket database cannot participate in one atomic transaction, so evidence rechecks are point-in-time checks rather than universal freshness or isolation guarantees. Local CLI confirmation and the local capability file are demonstration controls, not production multi-user authentication.

If the CLI is interrupted after exact execution confirmation, the ticket may already exist and the Action outcome may be `Unknown`. Do not rerun `execute`. Inspect `incidentgraph action status <action-id> --home <action-home>` and `incidentgraph action tickets --home <action-home>` to review the persisted Action state and ticket receipt. An `Unknown` Action is terminal; an observed ticket without a verified Runtime receipt must be reviewed before any manual follow-up.

#### Phase 3 setup and manual walkthrough

Use the prerequisites in [Local setup](#local-setup), including a running project FalkorDB container and local Ollama with `phi4-mini:latest`. Initialize a **new, unused** consumer-owned data directory. By default Windows uses `%LOCALAPPDATA%\IncidentGraphAgent\phase3-actions`; set `INCIDENTGRAPH_ACTION_HOME` or pass `--home` to choose another private local directory. Do not copy these SQLite files into the repository or edit the capability/identity files.

The following is a manual walkthrough; it was not run automatically as part of this implementation. Keep each command separate and review its JSON output before proceeding:

```powershell
uv run --locked --env-file .env --python 3.13 incidentgraph action init
uv run --locked --env-file .env --python 3.13 incidentgraph action propose incident-payment-001
# Copy run_id from the proposal JSON and replace this quoted placeholder.
uv run --locked --env-file .env --python 3.13 incidentgraph action review "paste-run-id-here"
uv run --locked --env-file .env --python 3.13 incidentgraph action approve "paste-run-id-here"
uv run --locked --env-file .env --python 3.13 incidentgraph action admit "paste-run-id-here"
# Copy action_id from the admission JSON and replace this quoted placeholder.
uv run --locked --env-file .env --python 3.13 incidentgraph action authorize "paste-action-id-here"
uv run --locked --env-file .env --python 3.13 incidentgraph action execute "paste-action-id-here"
uv run --locked --env-file .env --python 3.13 incidentgraph action status "paste-action-id-here"
uv run --locked --env-file .env --python 3.13 incidentgraph action tickets
```

At the interactive prompts, type the complete displayed challenge exactly: `APPROVE <plan-digest>`, `AUTHORIZE <action-id>`, or `EXECUTE <action-id>`. Approval alone never authorizes execution. Review and verify the ticket receipt returned by `execute`; the `status` and `tickets` commands read the persisted local databases after the process has closed and reopened them. This demonstration creates one reversible local SQLite record and does not modify the incident graph or remediate a service.

### Phase 4 local browser console

The local Web Console presents four views: Graph Explorer, AI Investigation, Action Approval, and Tickets & Audit. It reads the live local FalkorDB graph and uses the configured local Ollama model for investigation. The deterministic answer is constructed from validated graph evidence; raw model prose is not used as the answer.

Start from the project root after completing [Local setup](#local-setup), with FalkorDB and Ollama available. Initialize a new private action home only once; if the chosen directory already exists, select a different directory and do not reset it:

```powershell
$actionHome = Join-Path $env:LOCALAPPDATA 'IncidentGraphAgent\phase4-demo'
uv run --locked --env-file .env --python 3.13 incidentgraph action init --home $actionHome
uv run --locked --env-file .env --python 3.13 incidentgraph web serve --home $actionHome --port 8765
```

Open `http://127.0.0.1:8765/` in a browser on the same computer. The listener binds to loopback and is not a public deployment. Use one process at a time with this private action home; do not run CLI Action commands against the same home while the Web Console is running. In the console, inspect the graph, run an investigation, propose a Plan, then review the exact Plan and evidence. Approval, admission, authorization, and execution are separate steps. The human operator must review and enter each exact confirmation phrase personally. The only supported effect is creating one local SQLite incident ticket; Agent and MCP graph tools stay read-only.

The [final human-approved demonstration](docs/demo/FINAL-DEMO-REPORT.md), completed on October 17, 2026, verified a real FalkorDB/Ollama investigation, exact owner-entered Plan Approval, separate Action Authorization and Execution confirmation, a persisted `Succeeded` Action, and one independently verified local SQLite ticket and receipt. [Watch the three-minute video](https://youtu.be/vdLB3sI8tSo). The official Graph Hacks submission is confirmed; see the [final submission checklist](docs/submission/FINAL-SUBMISSION-CHECKLIST.md).

Final screenshots: [complete investigation](docs/demo/screenshots/final-investigation.png), [human-approved Plan](docs/demo/screenshots/final-approved-plan.png), and [persisted ticket and verified receipt](docs/demo/screenshots/final-ticket-audit.png). The chronological video uses real embedded-browser captures with local English TTS and captions. The owner entered confirmations between captured takes; the video shows persisted states and does not claim continuous footage of typing. Source frames are 964×463, upscaled to a 1920×1080 export.

The [frontend polish report](docs/FRONTEND-POLISH-REPORT.md) records the current console's visual review, action-control fixes, and responsive verification limits.

The initial Phase 4 walkthrough stopped before approval and remains historical evidence: [Graph Explorer](docs/demo/screenshots/01-graph-explorer.png), [real Ollama investigation](docs/demo/screenshots/02-real-ollama-investigation.png), [Plan awaiting approval](docs/demo/screenshots/03-action-review-awaiting-approval.png), and [original structured evidence](docs/demo/evidence/README.md).

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

The Phase 3 snapshot and approval-gated Runtime integration tests use the real local FalkorDB service and isolated temporary Runtime, evidence, and ticket stores:

```powershell
uv run --locked --env-file .env --python 3.13 pytest -q tests/test_action_cli.py tests/test_action_evidence.py tests/test_ticket_actions.py
```

Historical evidence: [Phase 0 closure](docs/PHASE0-CLOSURE.md), [Phase 1 report](docs/PHASE1-REPORT.md), [Phase 1.1 report](docs/PHASE1.1-REPORT.md), [Phase 1.2 report](docs/PHASE1.2-REPORT.md). Phase 2 implementation evidence is in [docs/PHASE2-REPORT.md](docs/PHASE2-REPORT.md), Phase 2.1 hardening evidence is in [docs/PHASE2.1-REPORT.md](docs/PHASE2.1-REPORT.md), Phase 3 implementation evidence is in [docs/PHASE3-REPORT.md](docs/PHASE3-REPORT.md), and the Phase 4 delivery state is recorded in [docs/PHASE4-REPORT.md](docs/PHASE4-REPORT.md).

Current limits: synthetic single-incident dataset; no live telemetry or incident ingestion; temporal correlation does not infer causality; traversal depth is at most four and each path query returns at most 500 rows; local single-user setup. Phase 3 creates only a local follow-up ticket; it does not call Jira, PagerDuty, or ServiceNow and does not remediate services. The Phase 4 Web Console is loopback-only and has no production authentication, multi-user authorization, public deployment, or distributed execution. Graph tools and MCP remain read-only; an Agent cannot authorize or execute an Action.
