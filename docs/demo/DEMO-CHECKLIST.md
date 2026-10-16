# Human-approved demonstration checklist

## Read-only preparation

- [ ] FalkorDB is healthy; graph catalog is loaded.
- [ ] Local Ollama reports `phi4-mini:latest` available.
- [ ] Web Console is bound to loopback on the local computer.
- [ ] Dedicated private Action home is initialized and its ticket count is 0.
- [ ] Run the investigation and inspect the paths, deployments, alerts, evidence completeness, and causality statement.
- [ ] Propose the Plan and inspect its exact arguments, target, risk class, Plan digest, evidence digest, and snapshot freshness.

## Human-controlled lifecycle

- [ ] Owner explicitly approves or rejects the exact Plan.
- [ ] If approved, admit the Run as an Action.
- [ ] Owner independently authorizes the exact Action.
- [ ] Owner independently confirms or declines exact execution.
- [ ] Inspect persisted Action status after every operation.
- [ ] If execution succeeds, verify the durable ticket and receipt; if outcome is `Unknown`, do not retry.

Current state: read-only preparation is evidenced; the approval input remains blank and the owner has not approved, admitted, authorized, or executed this Action. All lifecycle checkboxes above remain unchecked.
