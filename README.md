# incidentgraph-agent

> **Phase 0 preflight, 2026-10-14 NZDT.** This repository contains preparation notes only. The product capabilities described below are planned and are not implemented.

## Competition

[Graph Hacks: Context for AI Agents — official rules](https://www.wemakedevs.org/hackathons/falkordb/rules)

**Track:** Agents That Act on Connected Data

The official hacking window is October 15, 2026, 12:01 AM IST through October 18, 2026, 11:59 PM IST. In New Zealand daylight time, that is **October 15, 2026, 7:31 AM NZDT** through **October 19, 2026, 7:29 AM NZDT**. The organizers may update the rules or schedule, so check the official page again at kickoff.

Submission checklist for later: public source repository, setup instructions and graph model, required Cypher or graph algorithms, demo video, live deployment or complete local setup, named track, and disclosure of AI coding assistants.

## Project concept — planned

Build an incident investigation agent that uses connected dependency data to trace relationships and assess potential impact. FalkorDB is planned as the primary graph database and must do central work in these workflows. Any future side-effect action will use the existing Agentic Intent Gate runtime's approval and separate execution-authorization boundaries.

**No application functionality is implemented yet.**

## Planned technology stack

- Python 3.13, with `uv` for isolated environments and dependency management.
- FalkorDB as the primary graph database, run locally with Docker Compose. Phase 0 smoke-tested the server-only image `falkordb/falkordb-server:6.0.2`, pinned by multi-architecture digest `sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0` (Linux/amd64 child manifest `sha256:e0b81c8e28c692f2857ea404dd6475dd6a861688660e0e529fc5acfb1ab1a558`). Recheck this image pin before Phase 1.
- Agentic Intent Gate `v0.2.0`, consumed as an external dependency at commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`.
- Frontend, model provider, and any additional libraries remain undecided until the competition begins.

The runtime dependency has **not** yet been declared in a project manifest or lockfile. It was installed only in a temporary isolated environment for Phase 0 verification. No runtime code has been copied into or changed in this repository.

## Planned functionality — not implemented

- Investigate incidents using connected graph context.
- Traverse dependencies and assess affected components.
- Use reviewed, explicitly authorized side-effect actions only if the final product requires them.

There is currently no graph schema, incident dataset, Cypher business query, runtime adapter, MCP integration, application code, or UI in this repository. These are future competition work, not existing features.

## Pre-existing work and competition work

- **Pre-existing:** Agentic Intent Gate is a separate upstream runtime project. This repository will consume its pinned public API; it will not copy or modify the shared runtime.
- **Pre-kickoff Phase 0 preparation here:** AGENTS.md and README preparation, Docker/FalkorDB infrastructure verification, Agentic Intent Gate dependency verification, and the [Phase 0 evidence report](docs/PHASE0-CLOSURE.md). No competition-specific application implementation was performed before kickoff.
- **New competition work:** application and graph functionality created after the official start time. The competition rules permit pre-event discussion and notes, while requiring the main coding and design work to start after kickoff.

## Local prerequisites

- Windows, VS Code, and Git.
- Python 3.13 and `uv`.
- Docker Desktop with the Linux Docker Engine running, plus Docker Compose.
- Network access to GitHub for the pinned runtime dependency and to the container registry for FalkorDB.

The Phase 0 report records which prerequisites are currently available on the development machine.
