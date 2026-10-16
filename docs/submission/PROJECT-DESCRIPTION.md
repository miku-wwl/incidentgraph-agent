# IncidentGraph Agent — project description

**Track:** Agents That Act on Connected Data

IncidentGraph Agent investigates synthetic service incidents through FalkorDB dependency paths, deployment records, and alerts. A bounded local Agent Loop selects from fixed read-only graph tools; the application validates graph evidence and constructs deterministic answers with traceable references. It reports possible impact without claiming that temporal correlation proves causality.

For follow-up, a deterministic Plan binds to a fresh graph evidence snapshot. The installed Agentic Intent Gate Runtime enforces separate human Plan Approval, Action Authorization, and exact Execution Confirmation. The sole registered side effect writes one ticket to a consumer-owned local SQLite store. The Phase 4 loopback Web Console exposes the graph, investigation, Plan review, separate gates, and ticket audit views. Agent and MCP graph tools remain read-only; no service remediation or external ticket provider is called.

The project uses a local FalkorDB 6.0.2 container, Python 3.13, local Ollama `phi4-mini:latest`, and the pre-existing Agentic Intent Gate v0.2.0 dependency pinned to commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`.
