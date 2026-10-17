# Final verified local demonstration

**Result: PASS — local human-approved lifecycle, published video, and official submission verified on October 17, 2026.**

[Three-minute demo video, unlisted](https://youtu.be/vdLB3sI8tSo) · [official authenticated submission receipt](https://www.wemakedevs.org/hackathons/falkordb/submit) · [submission checklist](../submission/FINAL-SUBMISSION-CHECKLIST.md)

## Scope and provenance

Code baseline: `38ba43626ab21ea730111cebb6a088087a5a53dc`, branch `main`. Git changes in this delivery are documentation and sanitized screenshots only. No source, dependency, graph data, Runtime, Docker configuration, or permission changes were made. The approved lifecycle wrote its ordinary Run, Action, snapshot, and single ticket records; no databases were reset or manually edited. Existing project services and the existing private demonstration home were used. The historical Phase 4 report describes an earlier incomplete walkthrough and is preserved unchanged.

All browser interaction used the same-session **Codex In-app Browser**, `type=iab`, browser ID `2`, local application tab ID `1`, at `http://127.0.0.1:8765/`. Browser session metadata matched the active conversation. No native desktop control, external browser automation, cloud browser, tunnel, deployment, or fabricated application frame was used.

## Verified investigation

- FalkorDB 6.0.2: actual module version `60002`, `PONG`; Graph Explorer displayed 8 services, 7 dependency edges, 1 incident, and 2 incident links.
- Incoming blast-radius traversal from `payment-db`: 6 paths and 4 potentially affected services (`order-api`, `payment-service`, `checkout-api`, `notification-service`).
- Actual local Ollama `phi4-mini:latest` inference selected the trusted read-only `investigate_incident` tool for `incident-payment-001`. The Task Contract completed with validated complete evidence and a deterministic application summary.
- Incident investigation identified `notification-service` as potentially affected through `notification-service → checkout-api` and `notification-service → checkout-api → payment-service`, with 2 deployments and 2 alerts. **Causality remained unproven.** This is synthetic incident data, not a verified production outage.
- [Investigation screenshot](screenshots/final-investigation.png).

## Exact Plan and Action identities

| Evidence | Verified value |
| --- | --- |
| Incident | `incident-payment-001` |
| Run | `99fe5c08-9404-4b82-944d-4012975b7489` |
| Plan digest | `58d06ca8a4c2dd8131122f062f58da2ccb16fa0232967c3fbd0f75606c1535af` |
| Evidence digest | `d0c2c6c4ff69080698da8d70f1d9e2c93def5a9bfd1ce58616ee947e3f21fe8d` |
| Observed snapshot | `2026-10-17T06:29:34.514943Z` |
| Trusted target | `incidentgraph-ticket-store-a35af093-2065-4475-a676-8dea7abe4eaf` |
| Tool / risk | `create_local_incident_ticket` v1 / `SIDE_EFFECT` |
| Action | `c4815234-33fb-44d6-b34c-3269f42ee37e` |
| Effect key | `3112138e-eb11-49fa-9849-da384661b8cd` |
| Ticket | `9d2c8051-b9d2-43b2-85d4-6f561722df56` |
| Receipt | `159e9244-63aa-495e-95c2-d1ed8474a293` |
| Payload digest | `b6454e02a821c5db48b9ac92a27e5dd5be95d0c079a245bcccd31eb51ff620d5` |

The exact four execution arguments were the incident ID, evidence digest, snapshot observation time above, and `affected_service_count=1`.

## Human gates and durable execution

1. The owner personally entered the exact Plan `APPROVE` phrase and reported that the page showed `APPROVED`. The assistant refreshed Review and verified the persisted approved Plan and freshness before admission. [Approved Plan screenshot](screenshots/final-approved-plan.png).
2. The assistant separately admitted that approved Run. The resulting Action was `Pending`, with no authorization and no observed effect.
3. The owner personally entered the exact `AUTHORIZE` phrase for this Action. The persisted audit independently records authorization.
4. The owner personally entered the distinct `EXECUTE` phrase and clicked execution. The owner explicitly confirmed this provenance after the assistant observed success. **The assistant did not enter or submit APPROVE, AUTHORIZE, or EXECUTE.**
5. Runtime Status persisted `Succeeded`, `EXECUTION_SUCCEEDED`, `effect_observed=true`, and `receipt_verified=true`. Tickets & Audit displayed one matching local ticket and receipt. No Action was replayed to improve footage.

Confirmation typing happened **between recorded takes**. The raw captures show the preceding gates and subsequent persisted states; the video and narration disclose this gap. The owner reports, exact persisted records, and audit support this local demonstration. They do not establish production human identity authentication.

Independent read-only SQLite inspection exported only selected non-secret evidence. A separate Python process opened and closed the ticket database twice using `mode=ro` and `PRAGMA query_only=ON`; both observations found exactly 1 ticket and exactly 1 matching effect row, the same ticket/receipt/target/tool/incident/digests, and `integrity_check=ok`. The canonical exported ticket record hash was `c3b2c757a1f025e9b11181b50a10491edb2a436421683774f68368b8e5c96aee`. This verifies reopening durable state; it is not a power-loss or whole-machine restart test.

The Runtime ledger was separately inspected read-only, exporting event type, version, and timestamp only:

| Event | UTC timestamp |
| --- | --- |
| ACTION_CREATED | `2026-10-17T06:32:27.050207Z` |
| ACTION_AUTHORIZED | `2026-10-17T06:35:20.965455Z` |
| EXECUTION_CLAIMED | `2026-10-17T06:35:24.931331Z` |
| EXECUTION_SUCCEEDED | `2026-10-17T06:35:25.048213Z` |

Exactly one durable execution claim was present. The ticket was created at `2026-10-17T06:35:25.016947Z`, before the ten-minute evidence snapshot expired. Later expiry does not invalidate the historical completed execution and does not permit reuse.

[Ticket and audit screenshot](screenshots/final-ticket-audit.png).

## Genuine browser recording and final media

Skill: `codex-iab-video-recording`, read from `C:\Users\weila\.codex\skills\codex-iab-video-recording\SKILL.md`.

The skill's `scripts/capture_iab.mjs` was imported inside the browser-capable REPL. `recordIabTake({tab, browserId, outputDir, seconds, targetFps, label, steps})` sampled real `tab.screenshot({fullPage:false})` frames while real DOM operations ran in the same awaited call. Its `scripts/render_iab.py` validated frame hashes and monotonic timestamps, rendered source-time clips, and FFmpeg assembled the final captioned/narrated MP4. Original frames and manifests remain outside Git.

Accepted raw takes: `07-wide-live-graph-query` through `15-durable-ticket-and-audit`. These retain complete source takes and hashes. Earlier expired-Plan attempts and the superseded draft video are preserved separately and excluded from the accepted final package.

| Final scene | Raw take | Source interval in seconds |
| --- | --- | --- |
| Graph | 07 | 0–20 |
| Real inference | 08 | 0–35 |
| Complete response | 09 | 0–20 |
| Fresh Plan review | 10 | 6–16 |
| Human approval gate | 11 | 0–24 |
| Approved Plan and admission | 12 | 0–14 |
| Independent authorization gate | 13 | 0–15 |
| Persisted success | 14 | 0–22 |
| Durable ticket and receipt | 15 | 0–20 |

Take 10 starts at second 6 in the final edit because the interface briefly retained previous Plan controls before Review refreshed the new Plan. The full original take is retained; no stale approval was submitted. Inactive waiting between takes is removed. No unrelated execution is represented as part of the successful Action.

- Source capture: **964×463 pixels**, actual per-take average **8.47–11.17 fps**, maximum inter-sample gap **1.033 seconds**. CSS viewport was 978×470. Export is upscaled; it is not native 1080p/30fps capture.
- Final file: `incidentgraph-agent-verified-demo.mp4`, **180.000 seconds**, **1920×1080**, H.264/yuv420p/tv, 30fps/5400 frames, AAC stereo 48kHz, **7,735,588 bytes**.
- SHA-256: `01e8ef2cce6577173cd18c42d5f0a5746d8a41042174b9667ace7cc98fbc37a6`.
- English narration: local Microsoft Zira Desktop TTS. Sentence PCM sample counts drive caption timing, with the same scene tempo adjustment as the audible track and a 250ms scene start delay. Largest tempo factor: 1.034.
- 34 English captions: burned into the video, embedded mov_text track, separate SRT, and uploaded YouTube English (US) captions. A thumbnail decoded from the final video is supplied in the artifact package; a custom YouTube thumbnail was not uploaded.
- Audio verification: non-silent, mean -19.2 dB, peak -1.4 dB; complete decoding passed. Human listening review was **NOT RUN**.
- Nine representative frames decoded from the final MP4 were visually inspected, including exact Plan, authorization, success, and ticket scenes. Text and captions were checked for readability and secrets.
- Browser playback: the actual HTTPS YouTube player advanced through the full video and reached its end with no media error. It reported approximately 180.021 seconds after YouTube transcoding, 1920×1080, and a ready media stream. This verifies playback, not universal access from every network or device.
- A `file://` playback attempt was rejected by browser security. No workaround or local serving route was created. Actual published HTTPS playback supplied the browser verification.

## Validation actually executed for this delivery

| Check | Result |
| --- | --- |
| Existing FalkorDB and Web Console health; real graph reads | PASS |
| Real local Ollama inference | PASS |
| Installed Python / Runtime pin and imports | PASS — Python 3.13.9, installed Runtime 0.2.0 at the pinned commit |
| `uv pip check --python .venv\Scripts\python.exe` | PASS — 46 packages compatible |
| Exact human gates, Runtime Status, matching durable ticket and receipt | PASS |
| Independent read-only ticket reopen and sanitized Runtime audit | PASS |
| Skill frame-hash/timestamp validation and FFprobe metadata | PASS |
| `D:\Buzz\_internal\ffmpeg.exe -v error -xerror -i <final MP4> -map 0:v:0 -map 0:a:0 -f null -` | PASS — full video/audio decode |
| Final decoded-frame visual inspection | PASS — 9 scene samples |
| Published YouTube HTTPS playback | PASS — reached end, no media error |
| Official competition submission | PASS — platform displayed SUBMITTED and exact links |
| New pytest / Ruff / Black / `uv sync --locked` runs | NOT RUN — no source or dependency changes in this delivery |

Historical Phase 4 implementation evidence records 166 tests passing in 122.21s, including real FalkorDB/MCP integration; 7 focused Web Console tests passing in 20.59s; Ruff, Black, and locked sync checks. These results are **historical**, not new test executions for this documentation/media delivery.

Runtime remains `agentic-intent-gate==0.2.0`, pinned to `7261fdd2f47d2b15fabe8faac527eeee37338eb1`; default five-second execution budget retained. FalkorDB image remains `falkordb/falkordb-server@sha256:8e4fe29e6646890a9deaf2f009ad2bc5e592d79fe9cd718810c3a97a8bcd34c0`.

## Publication, submission, and limits

YouTube publication to the existing channel was authorized by the owner, including explicit action-time acceptance of upload terms. Visibility is **Unlisted**, link playable without a selected viewer grant. Synthetic narration/AI assistance was disclosed. YouTube copyright checks reported no issues at publication; this is not a legal guarantee.

The official [WeMakeDevs submission page](https://www.wemakedevs.org/hackathons/falkordb/submit) displayed **SUBMITTED**, project **IncidentGraph Agent**, submission time **October 17, 2026, 19:56 NZDT**, and the exact repository and video links. This authenticated receipt URL is not claimed as a public project permalink. No separate public project permalink was exposed by the receipt. The track is stated in the submitted description; the actual form did not provide a separate track selector. The optional deployment field is blank because complete local setup is supplied.

The [official rules](https://www.wemakedevs.org/hackathons/falkordb/rules), rechecked on October 17, set the window to October 15, 07:31 NZDT through **October 19, 07:29 NZDT**. The public repository, setup, graph model/Cypher, track, video, AI-assistance disclosure, and pre-existing Runtime provenance were included. Registration and the authenticated submission state were observed. Acceptance by judges or prize eligibility is not established by this technical receipt.

Remaining limitations:

- Authorization-section button is mislabeled “Execute Action”; the actual authorization callback and distinct execution gate remain separate. Disclosed before the owner authorized; no source fix was made for filming.
- The console briefly retains prior Plan controls until refreshed Review after a proposal. No stale gate was used; the final edit excludes this transition while retaining the raw take.
- Synthetic single-incident data, bounded traversal, point-in-time completeness checks, no causal proof, no live telemetry or external ticket provider.
- Single-user loopback console, no production authentication or public deployment; separate graph/Runtime/ticket stores are not one atomic transaction.
- Human confirmation provenance combines owner reports with persisted state and audit; continuous typing footage and production identity proof are absent.
- No infrastructure remediation, new Action type, Runtime modification, consent automation, database reset, or new development phase was performed.

**Delivery blockers: none for this local demo, video, and organizer submission.** Presentation defects and production limitations above remain. No claim of production readiness is made.
