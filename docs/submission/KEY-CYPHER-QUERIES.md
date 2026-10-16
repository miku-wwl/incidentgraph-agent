# Key Cypher queries and graph algorithms

All query templates are fixed in `src/incidentgraph_agent/repository.py`; identifiers and limits are bound as parameters. Model or browser input cannot supply Cypher. Traversal is limited to four hops, excludes repeated nodes within a path, and uses bounded result collection. Path and evidence queries fetch up to 501 rows as a sentinel; at most 500 rows are returned, and a 501st row fails closed as `incomplete_graph_evidence`. The server's mutable `RESULTSET_SIZE` is checked around graph reads and must be unlimited or at least 501.

## Service dependency paths

```cypher
MATCH p=(source:Service {id: $service_id})-[:DEPENDS_ON*1..4]->(target:Service)
WHERE length(p) <= $max_depth
  AND all(node IN nodes(p) WHERE single(candidate IN nodes(p) WHERE id(candidate) = id(node)))
RETURN [n IN nodes(p) | n.id] AS path_ids, length(p) AS depth
LIMIT $limit
```

## Blast radius

```cypher
MATCH p=(dependent:Service)-[:DEPENDS_ON*1..4]->(failed:Service {id: $service_id})
WHERE length(p) <= $max_depth AND dependent.id <> failed.id
  AND all(node IN nodes(p) WHERE single(candidate IN nodes(p) WHERE id(candidate) = id(node)))
RETURN dependent.id AS service_id,
       [n IN nodes(p) | n.id] AS path_ids,
       length(p) AS depth
LIMIT $limit
```

Distinct affected-service totals are derived from the complete path set; there is no separate count query. A truncated or invalid result is not presented as complete evidence.

## Incident evidence

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

Evidence timestamps are compared as timezone-aware instants. A deployment before an alert and incident is described as a lead only; the product explicitly reports causality as unproven.
