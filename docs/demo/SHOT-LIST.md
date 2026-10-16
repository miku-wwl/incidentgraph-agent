# Demo recording shot list

1. Local Web Console header and FalkorDB health (`PONG`).
2. Full service dependency graph and one incident node.
3. Selected service/incident details and directed relationship explanation.
4. Local model badge showing `phi4-mini:latest`.
5. Investigation summary, deterministic source, complete evidence references, two verified dependency paths, and unproven causality.
6. Proposed Run in `AwaitingApproval`; show exact Plan, exact target, both digests, risk class, snapshot freshness, and affected-service count.
7. Owner at the approval gate. Capture only the owner's actual choice; do not prefill or type the phrase for them.
8. If approved, separately show Action admission and the authorization gate; capture the owner's independent choice.
9. If authorized, separately show exact execution confirmation and the owner's independent choice.
10. Read persisted Action status, ticket list, and verified receipt. If execution did not happen, show the real state and empty ticket store.

Never show the capability file, environment variables, private SQLite paths, browser profile contents, credentials, or unrelated desktop windows. Do not show a successful receipt unless one was actually verified from persisted state.
