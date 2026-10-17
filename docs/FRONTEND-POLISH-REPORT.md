# Frontend Polish Report

**Result: PARTIAL** — frontend changes and regression checks pass. Browser screenshots were captured and visually reviewed, but the browser security policy blocked exporting them as local files. The requested 1920×1080, 1440×900, and 1280×720 viewport checks were also unavailable in the supported browser controls.

## Scope and files

Baseline: `main` at `038bd324b7dd643240df34480915f7f06cb642c3`.

Changed files:

- `README.md`
- `src/incidentgraph_agent/web/index.html`
- `src/incidentgraph_agent/web/style.css`
- `src/incidentgraph_agent/web/app.js`
- `tests/test_web_console.py`
- `docs/FRONTEND-POLISH-REPORT.md`

No backend, Runtime, dependency, lockfile, Docker, graph query, graph data, Action, ticket, or submission evidence was changed. At report preparation, no commit or push had been made.

## Product UI changes

- Removed the `PHASE 4 · DEMO` badge and competition label from product chrome. The header now says **IncidentGraph Agent / Connected Incident Intelligence**, identifies the loopback environment, and displays **FalkorDB connected** only when the health endpoint says `ready` and Redis reports `PONG`.
- Retained the accurate disclosure that this is a local console using synthetic incident data.
- Improved text contrast and sizing, navigation focus indicators, graph node focus/selection contrast, evidence cards, Action state labels, ticket identifiers, and receipt details. Added compact-height and desktop-width layout rules without changing the four views.
- Replaced title-based confirmation button selection with explicit labels: **Approve Plan**, **Authorize Action**, and **Execute Action**. The exact typed phrases and separate `/api/actions/decision`, `/api/actions/authorize`, and `/api/actions/execute` operations remain unchanged.
- Added immediate clearing of old Plan/Action state when a new identity is entered or a review/status/proposal begins. A request sequence check prevents an older response from restoring controls for a different ID.
- Snapshot display now uses `age unavailable` instead of rendering an empty age as `nulls`.
- Updated the README's obsolete authorization-button defect note to link to this report. Historical phase and demo reports were left unchanged.

## Browser verification

The existing local Codex In-app Browser tab at `http://127.0.0.1:8765/` loaded the edited static assets. Its measured CSS viewport was **979×470**; `document.documentElement.scrollWidth` was **964**, so no horizontal overflow was present at that viewport. The tab showed the product brand, `LOCAL · LOOPBACK`, and a health-backed `FalkorDB connected` status.

Observed read-only data:

- Graph Explorer reported 8 services, 7 dependency edges, 1 incident, and 2 incident links. A live blast-radius query from `payment-db` displayed 6 verified paths covering 4 affected services.
- The local `phi4-mini:latest` investigation completed with a deterministic evidence summary, two `notification-service` paths, related deployment and alert records, and `causality=unproven`.
- The Action view loaded the existing Run `99fe5c08-9404-4b82-944d-4012975b7489` read-only. It showed an already-approved Run with a stale snapshot. Changing the Run ID immediately removed its Plan details and `Admit approved Run as Action` control; the action-control list was empty and the state returned to `NO RECORD LOADED`. No approval, admission, authorization, or execution operation was submitted.
- Tickets & Audit still showed the pre-existing single ticket and Action `c4815234-33fb-44d6-b34c-3269f42ee37e` as `Succeeded`, with `receipt_verified=true` and `effect_observed=true`. No ticket or Runtime record was created or modified.
- Authorization label-to-endpoint mapping is covered by the focused regression test. The existing isolated full-lifecycle test exercises authorization and execution through separate endpoints. No new Action was created to display an authorization prompt in the persistent demo home.

Fresh screenshots of Graph Explorer, AI Investigation, Action Approval, and Tickets & Audit were captured with the browser screenshot API and visually inspected in the session. **No PNG files were written to the repository.** The browser URL policy rejected the `data:` URL transfer needed to export screenshot bytes, and no alternate browser or desktop export route was used. The captured images therefore cannot be included in the review ZIP at this time.

The supported browser API exposed no viewport override. Visual checks at **1920×1080**, **1440×900**, and **1280×720** were **NOT RUN**. The responsive CSS breakpoints were inspected, but that does not substitute for visual verification at those sizes.

## Tests and checks

All commands below were executed against the final frontend/test changes. The `.env` file was loaded for FalkorDB integration tests; its contents were not printed.

| Command | Result |
| --- | --- |
| `uv sync --locked --python 3.13` | PASS — 47 packages resolved, 46 checked |
| `uv run --locked --no-sync --env-file .env --python 3.13 pytest -q tests/test_web_console.py` | PASS — 9 passed in 22.33s |
| `uv run --locked --no-sync --env-file .env --python 3.13 pytest -q` | PASS — 168 passed in 126.70s |
| `uv run --locked --no-sync --python 3.13 ruff check src/incidentgraph_agent tests` | PASS |
| `uv run --locked --no-sync --python 3.13 black --check tests/test_web_console.py` | PASS — unchanged |
| `uv pip check --python .venv\Scripts\python.exe` | PASS — 46 packages compatible |
| `node --check src/incidentgraph_agent/web/app.js` | PASS |
| `git diff --check` | PASS |
| Search of frontend assets for phase, prototype, development, preview, test-version, and Graph Hacks labels | PASS — no matches |

One initial focused pytest invocation omitted `--env-file .env` and stopped during fixture setup with 9 configuration errors because `FALKORDB_PASSWORD` was unavailable in the process environment. The corrected invocation above passed; test fixtures use UUID-named isolated graphs and remove only their own graph.

## Remaining limitations and delivery state

- The three requested desktop viewport sizes were not visually verified because the supported in-app browser session did not expose a viewport-resize control.
- The browser policy prevented writing the four captured screenshots to local files. The ZIP is therefore a **partial review package** containing the changed README, source, tests, and this report; it does not contain screenshot PNGs.
- The exact confirmation-button UI was verified from the served source and regression test. Existing Runtime endpoint separation is covered by the integration suite; no additional protected UI operation was attempted during visual review.
- Existing synthetic-data, local-only, single-user, and non-remediation limits remain unchanged.

At report preparation, Git was uncommitted and unpushed. The expected working-tree status contained the five modified files above plus this report.

## Final async race fix (2026-10-17)

**Finding: reproduced and fixed.** Before the fix, a controlled Node DOM/fetch harness started authorization for `action-A`, changed the selected Action to `action-B`, then completed the first job. The page still selected `action-B` but displayed `AUTHORIZED` and restored an `Execute Action` control for `action-A`. The equivalent Run test selected `run-B` but displayed `APPROVED` and restored the `Admit approved Run as Action` control for `run-A`.

The cause was that `watchJob()` called lifecycle completion callbacks without checking whether the Run or Action that started the operation was still the current UI context. Existing lookup-generation checks protected read responses and Plan proposal state, but not approval, rejection, admission, authorization, or execution completion callbacks.

The fix captures the generation and expected identity when an operation starts. Run operations also retain the Plan digest; Action operations retain the Action ID and expected state. `watchJob()` verifies that context immediately before applying a result. Stale results do not call lifecycle callbacks or restore controls. The frontend continues polling the already-submitted job; changing a selection does not cancel backend work. A stale completion writes a neutral notice to the toast and job status, identifies the original Run or Action, and directs the operator to persisted status and receipt. Poll errors, failed jobs, and `Unknown` results use conservative wording. No automatic retry was added. The POST body uses the captured Run/Action ID rather than rereading a possibly changed field.

An executable Node harness uses the actual `app.js`, a minimal DOM, deferred job/status promises, and mocked HTTP responses. It covers nine scenarios: stale authorization, approval, rejection, admission, Plan proposal, and execution; the normal four-gate lifecycle; overlapping status requests; and stale `Unknown` or job-poll error outcomes. It asserts that stale controls remain cleared, the original identifier stays available in the notice, no duplicate request is submitted, and the normal sequence remains Approve → Admit → Authorize → Execute. The harness is invoked by `tests/test_frontend_async.py` and does not access the local Runtime or persistent demo stores.

### Async-fix validation

| Command | Result |
| --- | --- |
| Pre-fix reproduction: `node tests/frontend_async_race_harness.cjs --probe` | Confirmed stale `AUTHORIZED` + Execute control for Action A after selecting B, and stale `APPROVED` + Admit control for Run A after selecting B |
| `node tests/frontend_async_race_harness.cjs` | PASS — all 9 asynchronous scenarios |
| `uv run --locked --no-sync --env-file .env --python 3.13 pytest -q tests/test_frontend_async.py tests/test_web_console.py` | PASS — 10 passed |
| `uv run --locked --no-sync --env-file .env --python 3.13 pytest -q` | PASS — 169 passed in 124.88s |
| `uv sync --locked --python 3.13` | PASS — 47 packages resolved, 46 checked |
| `uv run --locked --python 3.13 ruff check src/incidentgraph_agent tests` | PASS |
| `uv run --locked --python 3.13 black --check tests/test_frontend_async.py` | PASS — unchanged |
| `uv pip check --python .venv\Scripts\python.exe` | PASS — 46 packages compatible |
| `node --check src/incidentgraph_agent/web/app.js` | PASS |
| `node --check tests/frontend_async_race_harness.cjs` | PASS |
| `git diff --check` | PASS |

The simulated normal lifecycle kept approval, admission, authorization, and execution as separate operations with their existing exact phrases. No Runtime dependency, backend route, ticket effect, Runtime approval rule, evidence validation, Agent, MCP permission, or FalkorDB query changed.

**Browser smoke: PARTIAL.** The existing local page opened and reported `FalkorDB connected`; the read-only Tickets & Audit view showed the pre-existing Action and ticket. No approval, admission, authorization, or execution request was submitted. The browser session exposed page state but no documented click/type control in this task session, so changing IDs and observing browser-native completion notifications was not run. Those async behaviors were exercised by the executable mocked DOM harness instead. No screenshot was fabricated or added.

### Async-fix delivery state

Baseline HEAD: `4a40f575e3a2bd70f4938f90cc3b1d5f50abd325` on `main`. At report preparation, the async fix was uncommitted and unpushed. The expected worktree changes at that time were this report, `src/incidentgraph_agent/web/app.js`, `tests/frontend_async_race_harness.cjs`, and `tests/test_frontend_async.py`. No historical submission evidence or private Runtime state was modified.
