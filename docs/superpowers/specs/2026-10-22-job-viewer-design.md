# Job Viewer — Design Spec

Date: 2026-10-22
Status: Draft, awaiting user review
Parent spec: `2026-10-22-character-swap-v1-design.md` (this pulls a read-only slice of Plan 3's UI forward)

## 1. Problem

Frank checks quality visually and will demo the workflow on YouTube. Today the preparation pipeline only produces files in `runs/<id>/` and JSON from `charswap status`; there is no way to see the steps, their outputs, why each step exists, or what they cost, without opening files one by one.

## 2. Goals / non-goals

Goals
- One local page per job that shows every preparation step's output (videos, audio, text), in order, with a plain-English "why this step exists" line — usable on camera to explain the technique.
- Live progress while a job is preparing.
- Links from each paid step to its exact run on Replicate.
- Costs summary per job and across jobs.
- Becomes the foundation of Plan 3's full UI (same stack, same app factory).

Non-goals
- Any action that changes state or spends money (prepare, approve, repair, generate). These stay in the CLI until Plan 3.
- Hosting beyond localhost, auth, multi-user.
- Generation results (Plan 2 adds sections for draft/final later).

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| V1 | FastAPI + Jinja2 templates + vanilla JS/CSS, served by `charswap view` | Same stack as Plan 3; nothing thrown away; live updates possible |
| V2 | Layout: fixed step navigator (left) + one tall scrolling page (right); clicking a step smooth-scrolls to its section; the section in view highlights in the navigator; costs only in the final section | Chosen by Frank via visual prototype (`.superpowers/brainstorm/…/layout-v3.html`) |
| V3 | Each section has a "Why this step?" line | Explains the technique on camera ("keep the motion, hide the identity") |
| V4 | Read-only, bound to 127.0.0.1, GET routes only | No accidental spending; not reachable from the network |
| V5 | Live progress by polling a cheap JSON endpoint every 3 s while the job is active | Simple; no websockets needed |
| V6 | The pipeline records `running` and `failed` stage statuses (small change to `pipeline._stage` and repair) | Needed to show ⟳/✕; was already on the Plan 2 prerequisite list |

## 4. User experience

`uv run charswap view [--port 8770] [--no-browser]` → starts the server, opens `http://127.0.0.1:8770`, prints the URL, Ctrl-C to stop.

**Home (`/`)**: table of jobs, newest first — short id, generator, mode, progress (e.g. "7/9 steps"), completed-steps cost estimate, created time. (No review status on home: computing it hashes every prepared video; it is shown on the job page.) Header shows all-jobs estimated spend. Empty state explains `charswap prepare <video>`.

**Job page (`/jobs/{id}`)**: header with job id, generator, mode, overall status, estimated cost so far.
Navigator (fixed): one entry per section relevant to the job's mode, with ✓ done / ◻ pending / ⟳ running / ✕ failed, then "💲 Costs". A section's status is derived from its stages (table below): failed if any stage failed; running if any running; done if all done; otherwise pending.

**Pipeline change (V6):** `pipeline._stage` marks the stage `running` (with `started_at`) before work and `failed` (with the error message) when it raises, then re-raises; `done` as today. It catches `BaseException` for this marking, so Ctrl-C records `failed` with message "interrupted" before re-raising (no stage is left `running` by a normal interrupt). `repair_with_rgb` does the same for composite/face_mesh/embed. `stage_done()` semantics are unchanged (only `done` counts), so resume still re-runs running/failed stages. Tests cover both new statuses.

**Stages per mode** (defines "N/M steps"):
| Mode | Stages (in order) | Count |
|---|---|---|
| depth | normalize, separate, pitch, transcribe, describe, depth, masks, composite, embed | 9 |
| depth_mesh | normalize, separate, pitch, transcribe, describe, depth, masks, composite, face_mesh, embed | 10 |
| face_mesh | normalize, separate, pitch, transcribe, describe, face_mesh, embed | 7 |
| direct | normalize | 1 |

**Section → stages:** Upload & normalize = normalize · Vocals = separate + pitch · Transcript & scene = transcribe + describe · Depth = depth · Mask & composite = masks + composite · Face mesh = face_mesh · Prepared video = embed · Source & character (direct) = normalize · Review = (none): its navigator status comes from `review.status(job)` — approved → ✓, rejected → ✕, otherwise ◻.

Sections by mode (only those that apply are rendered):

| # | Section | Shows | Modes | Why this step? (draft copy) |
|---|---|---|---|---|
| 1 | Upload & normalize | `upload.mp4` beside `original.mp4` (normalized); seconds, size | all | "Every clip is converted to 24 frames per second, upright and at most 960 px, so every later step lines up frame by frame." |
| 2 | Vocals | audio players: original audio (`upload.mp4`), `vocals.wav`, `vocals-plus3.wav` | depth, face_mesh, depth_mesh | "The voice is separated from the music, then raised 3 semitones. The AI still hears the exact words to move the lips, but not the original voice or song." |
| 3 | Transcript & scene | `state.transcript`, `state.scene`, `scene.jpg` | depth, face_mesh, depth_mesh | "The spoken words and a description of the room (never the people) go into the prompt, so the AI keeps the setting and lip-syncs the right words." |
| 4 | Depth | `depth.mp4` | depth, depth_mesh | "Depth turns everything into colored shapes by distance. The AI can no longer tell who the person is, but can still see exactly how they move." |
| 5 | Mask & composite | `mask.mp4`, composite (`composite-silent.mp4` for depth; for depth_mesh: `depth-composite-silent.mp4` once it exists, otherwise `composite-silent.mp4` while face_mesh is not yet done) | depth, depth_mesh | "SAM 3 cuts out the person. Only the person is replaced by depth; the real background stays, so the AI doesn't just trace an outline." |
| 6 | Face mesh | mesh video (`composite-silent.mp4`), tracking summary from `face-tracking.json` | face_mesh, depth_mesh | "A wireframe tracks 478 points on the face so the new character copies every expression and lip movement." |
| 7 | Prepared video | `prepared.mp4` | depth, face_mesh, depth_mesh | "This is exactly what the AI receives: motion and pitched voice, without the original identity." |
| 7w | Source & character | `original.mp4` + character image | direct (Wan) | "Wan copies motion straight from the original video onto your character — no disguise needed." |
| 8 | Review | review status (pending/approved/rejected/unavailable), note, fingerprint (short), the CLI command to approve | all | "Nothing is sent to the paid generator until you've watched the whole prepared clip and approved it." |
| $ | Costs | see "Costs" below | all | — |

Each paid step section (vocals, transcript & scene, depth, mask) shows "View on Replicate ↗" linking to `https://replicate.com/p/<prediction id>` read from the step's saved prediction record (`demucs-prediction.json`, `whisper-prediction.json`, `describe-prediction.json`, `depth-prediction.json`, `sam/sam-prediction.json`). After an RGB repair (`mask_source == "rgb"`) the mask section links both: "first attempt ↗" (`sam/`) and "repair ↗" (`rgb-repair/sam-prediction.json`). Missing record, unreadable record, or a `{"status": "creating"}` marker without an `id` → no link. Hugging Face depth has no record → no link.

**Costs section.** Rows are derived, not stored: for each paid stage of the job's mode (`pipeline.PAID_STAGES`) that is `done`, a row with step, provider ("Replicate"; Hugging Face depth shows "Hugging Face · free" at $0), estimate from `costs.PRICES[costs.STAGE_PRICE[stage]]` (via `costs.estimate_stages`), and the run link. Footer: "This job, steps completed so far ≈ $X" (sum of rows — so it is non-zero during a live or failed prepare; when `state["depth_provider"] == "huggingface"` the depth row is special-cased to $0 instead of calling `estimate_stages` for it); "Logged for this job: $Y" (sum of `state["costs"]` usd, which only appears after a successful prepare/repair); "All jobs (spend log): $Z" from `costs.total_spend(settings.runs_dir)`. Disclaimer: "Estimates. Exact charges: replicate.com billing." The header's "estimated cost so far" uses the completed-steps figure.

Missing files for a done step → the section shows "file missing" instead of breaking the page. Steps not yet done show a placeholder "Waiting for this step…".

Live progress: while the job is *active* (any stage `running`, or not all stages done and none failed), the page polls `/api/jobs/{id}` every 3 s; when any stage status changes, it reloads the page. Polling stops when all stages are done, any stage failed, or nothing has changed for 10 minutes (a client-side timer; reloading the page restarts it) — then the page shows "No progress — the job may have stopped. Run `charswap resume <id>`." A failed stage shows its error message in its section and the same resume hint.

## 5. Architecture

```
src/character_swap/web/
├── __init__.py
├── app.py          create_app(settings) -> FastAPI; routes; 127.0.0.1 only via cli
├── view_model.py   pure functions: job → sections, navigator entries, costs table, replicate links
├── templates/      base.html, jobs.html, job.html (Jinja2)
└── static/         viewer.css (tokens: light/dark), viewer.js (nav click-scroll, scroll-spy, polling)
```
- `view_model.py` holds all logic (which sections, statuses, links, "why" copy) and is unit-tested without HTTP. Templates only render.
- `cli.py` gains `view`: checks the port is free (bind test on 127.0.0.1), prints the URL, opens the browser unless `--no-browser`, then runs `env.serve(app, host="127.0.0.1", port=port)`. `Env` gets a new field `serve` (default: `uvicorn.run`); `main()` gets a matching optional `serve=` kwarg so tests inject a fake. `_view` catches `KeyboardInterrupt` itself → prints "Viewer stopped." and returns 0 (it must not reach `main`'s resume-hint handler). Port busy → prints the busy-port message to stderr and returns 2 directly.
- Section definitions live in one table in `view_model.py` (id, title, why text, modes, files, stage keys, record path).

Routes (GET only):
- `/` jobs list
- `/jobs/{id}` job page (404 page for unknown/invalid id)
- `/jobs/{id}/files/{name:path}` serves an artifact: only allowlisted extensions (`.mp4 .wav .jpg .jpeg .png .webp`; JSON is read server-side, never served), resolved via `Job.path` (containment), with HTTP Range support for video seeking (Starlette `FileResponse`)
- `/api/jobs/{id}` JSON: `{stages: {name: status}, done_count, total_count, active: bool}` — reads `state.json` only (no hashing, no review)
- `/static/...`

Review status on the page: computed with `review.status(job)` on page load (hashes files once per load; acceptable for a local viewer). The polling endpoint never hashes.

Dependencies added: `fastapi`, `uvicorn`, `jinja2`.

## 6. Error handling

- Unknown or malformed job id → 404 page with a link home.
- Corrupt `state.json` → job listed as "unreadable" on home; job page shows the error message instead of crashing.
- File route: disallowed extension, path escaping the job folder, or missing file → 404 (never 500, never a directory listing).
- Port in use → CLI prints "Port 8770 is busy; try `charswap view --port 8771`" and exits 2.

## 7. Testing

- `view_model` unit tests: sections per mode (depth, face_mesh, depth_mesh, direct), statuses from stage marks, Replicate link extraction (present / missing / rgb repair), costs table totals, "why" text present for every section, missing-file handling.
- Pipeline tests: a stage that raises is left `failed` with its message; a stage in progress is `running` (observed via a work function that inspects state); resume still re-runs `running`/`failed` stages.
- Route tests with FastAPI `TestClient`: home lists jobs newest first and empty state; job page contains every section id for its mode and the navigator anchors; 404 for bad id; file route serves an mp4 with `Accept-Ranges` and honors a `Range` request (206); file route rejects `../`, absolute paths, `.env`, unknown extensions; `/api/jobs/{id}` shape; no route accepts POST/PUT/DELETE (405).
- CLI: `view --no-browser` builds the app and calls an injectable server runner with host 127.0.0.1 and the port; busy port message.
- JS behaviour (click-to-scroll, scroll-spy, polling) is small and verified manually with a checklist (no JS test framework added).
- Coverage gate (≥ 85 %) still applies.
- Manual browser checklist for Frank: open viewer, click each navigator entry, scrub a video, toggle dark mode, run a live prepare and watch steps tick.

## 8. Out of scope / later

- Approve/reject/repair/generate buttons (Plan 3).
- Draft/final generation sections, face-asset panel (Plan 2/3).
- Stat-keyed fingerprint cache (Plan 3, when status is polled).
