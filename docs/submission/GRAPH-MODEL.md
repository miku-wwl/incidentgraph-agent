# Graph model

All bundled graph records are deterministic synthetic data in `src/incidentgraph_agent/synthetic_incident.json`.

| Node | Key properties |
| --- | --- |
| `Service` | `id`, `name`, `environment`, `criticality` |
| `Deployment` | `id`, `version`, `deployed_at`, `status` |
| `Alert` | `id`, `severity`, `timestamp`, `description` |
| `Incident` | `id`, `started_at`, `status`, `summary` |
| `Team` | `id`, `name` |

| Relationship | Meaning |
| --- | --- |
| `(:Service)-[:DEPENDS_ON]->(:Service)` | Source service requires target service. Impact traversal follows incoming edges from a failed service. |
| `(:Deployment)-[:DEPLOYED_TO]->(:Service)` | Deployment applies to a service. |
| `(:Alert)-[:ALERTS_ON]->(:Service)` | Alert concerns a service. |
| `(:Incident)-[:INVOLVES]->(:Service)` | Incident involves a service. |
| `(:Service)-[:OWNED_BY]->(:Team)` | Team owns a service. |

The checked-in seed contains 8 services, 2 deployments, 2 alerts, 1 incident, 4 teams, and 21 relationships. The graph is the source for dependency traversal, impact analysis, and incident evidence correlation; it is not a display-only layer. The graph contains no logs, traces, metric time series, or live incident feeds, so causal conclusions are not supported.

For the exact fixed Cypher statements and result completeness behavior, see [KEY-CYPHER-QUERIES.md](KEY-CYPHER-QUERIES.md) and the README's [Queries and example results](../../README.md#queries-and-example-results).
