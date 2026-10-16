# Demo video script (planned recording)

**Target length:** about 3 minutes
**Recording status:** not recorded
**Required operator:** project owner, who must make any approval, authorization, and execution decisions personally.

Do not record this as a completed-action demo until the owner has reviewed the Plan and independently decided to proceed through each Runtime gate. Never type or narrate a consent phrase on the owner's behalf. If the owner declines or the action becomes `Unknown`, stop and accurately show that state.

| Time | Screen / narration |
| --- | --- |
| 0:00–0:20 | Introduce IncidentGraph Agent and the Agents That Act on Connected Data track. State that the graph contains synthetic incident evidence and that the demo runs locally. |
| 0:20–0:55 | Open Graph Explorer. Show service nodes, dependency direction, incident links, and the live FalkorDB health indicator. Explain that a path is evidence of connectivity, not causality. |
| 0:55–1:30 | Select the payment incident and run local phi4-mini investigation. Show the two verified paths to `notification-service`, related deployment and alerts, deterministic answer source, and `causality: unproven`. |
| 1:30–2:00 | Propose an Action and open the full Plan review. Show Run ID, exact target, risk class, Plan digest, evidence digest, snapshot age, affected service count, and paths. Explain the only effect is one local SQLite incident ticket. |
| 2:00–2:20 | **Pause for the owner.** The owner reviews the Plan and makes an independent approve or reject decision. Do not type or submit for them. If approved, show the persisted result. |
| 2:20–2:40 | If and only if the owner chose approval, show separate Action admission and the exact Action ID. Pause again for the owner to decide whether to authorize. |
| 2:40–3:00 | If and only if separately authorized, pause for the owner to decide whether to execute. Then inspect persisted Action status, ticket, and verified receipt. If the owner does not proceed, show the actual terminal/pending state and do not imply a ticket exists. |

The current evidence ends at 2:00: a real Plan was reviewed, but no human gate was passed. This script is a recording plan, not a claim that the video or end-to-end execution exists.
