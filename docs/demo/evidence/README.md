# Phase 4 local demonstration evidence

These artifacts record a real local browser session on October 17, 2026, using an isolated Chrome profile, the local Web Console, the project FalkorDB graph, and local Ollama `phi4-mini:latest`.

## Observed

- The browser opened only `http://127.0.0.1:8765/` in the isolated profile.
- FalkorDB reported graph module 60002 and `PONG`; the catalog showed 8 services and 1 incident.
- The local model completed the investigation. The application returned deterministic graph-grounded output with two paths to `notification-service`; causality remained unproven.
- Run `a6829085-e003-4cef-94c5-522802bdfddd` was created in a dedicated private Phase 4 action home and reviewed.
- The Plan digest was `9a64f25a8a4b4c45ad01c511e540b4e925af9165b437bdd817d249bf422f5df3`; the evidence digest was `5dbc9d641402abc8a82b512188ca94bb4da53e085731a48409ed1c8e1df99613`.
- The Run remained `AwaitingApproval`. The approval input was blank; no human approval, admission, authorization, execution, ticket, or receipt occurred. Ticket count before approval was 0.

## Files

- `browser-review.json` — structured browser observations, full proposed Plan, evidence, and explicit no-approval marker.
- `cli/` — captured read-only graph checks, dependency/path results, deterministic incident investigation, and actual local Ollama investigation output.
- `../screenshots/` — three screenshots of the graph, investigation, and pending approval review.

The recorded graph records are synthetic project data. These files do not contain capability tokens or private SQLite files. They are evidence of a local run only; they do not show a completed Action lifecycle or a human approval.
