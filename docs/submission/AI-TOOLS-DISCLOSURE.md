# AI tools and pre-existing runtime disclosure

- **AI coding assistance:** OpenAI ChatGPT and Codex were used to assist with implementation, tests, documentation, code review preparation, and evidence packaging. The project owner remains responsible for reviewing, understanding, and explaining the submitted work.
- **Inference during the local demo:** Ollama `phi4-mini:latest` ran locally on the project owner's computer. It selected bounded graph tools. The application created the displayed answer from validated graph results; raw model prose was not treated as the answer.
- **Pre-existing shared runtime:** Agentic Intent Gate v0.2.0, pinned to immutable commit `7261fdd2f47d2b15fabe8faac527eeee37338eb1`, predates this competition application and is consumed as a dependency. Its source was not copied or modified for this submission. The consumer-specific ticket executor, graph product, Web Console, and integration were developed in this repository.
- **Data:** The bundled incident, service, deployment, and alert records are synthetic. No private or restricted incident data was loaded for this demo.
