# Activity journal

## 2026-10-22 — Session 1: fork, design, plan
- **Goal:** Turn a friend's MIT character-swap workflow into Frank's own project.
- **Decisions:** PiAPI Seedance 2.5 less-restriction as main generator (real faces via reviewed asset library, draft→1080p promote); Wan 2.2 Animate on Replicate as open-source option; Seedance-on-Replicate dropped (blocks real faces). R2 presigned hosting + tmpfiles fallback. All three prep modes kept. uv + src layout + 85 % coverage gate.
- **Lessons:** Real-face access is granted per provider account by ByteDance ("Advanced Creation Rights", enterprise only); resellers expose it under different names.

## 2026-10-22 — Session 1 (cont.): Plan 1 built (subagent-driven)
- **Goal:** Build Plan 1 (foundation + preparation pipeline) with per-task spec + quality review.
- **Completed:** config, jobs, media (video/audio/composite), Replicate client, stages (Demucs, Rubber Band, Whisper, scene description, depth, SAM 3, face mesh), review gate, costs, prompts, resumable pipeline + RGB repair, `charswap` CLI. 239 tests, 96.3 % coverage, ruff clean. Final review: ready to merge.
- **Decisions:**
  - Replicate client writes an exclusive "creating" marker before every paid POST, so ambiguous outcomes (timeout, 5xx, disk full) can never lead to a silent second charge. Why: reviewer showed several double-charge paths in the plan's version.
  - Expired output links vs network blips are distinguished; only expiry tells the user to delete a record (which costs money).
  - Untrusted text (Whisper transcript, scene description) has every `@` stripped and the prompt is capped at 4000 chars. Why: a stray `@image3` would make PiAPI reject a paid request after review.
  - Phone rotation metadata is applied in normalize. Memory stays at one frame (was ~1.1 GB for a 30 s clip).
  - Repair resets composite/embed(/face_mesh) marks before touching files so a crash can't leave a stale prepared video to be approved.
  - CLI is agent-safe: errors on stderr, Ctrl-C → resume hint (exit 130), resume confirms remaining paid steps.
- **Lessons:** Writing complete code in the plan + two-stage review caught ~40 real defects before any live API call. Subagents often skip "watch the test fail"; ask reviewers to verify with mutation checks.

## 2026-11-01 — Job viewer (read-only, `charswap view`)
- **Goal:** Let Frank watch a job's steps and review its results in a browser without touching the CLI flow.
- **Built:** `web/` (FastAPI + Jinja2): jobs list, job page with fixed navigator and tall sections, costs, live polling; `_tracked()` in the pipeline now records running/done/failed per stage (repair SAM tracked under composite). 389 tests, 97 % coverage, ruff clean. Smoke start passed (home/job 200, Range 206, API JSON, foreign Host 400, SIGINT exit 0 with "Viewer stopped.").
- **Decisions:**
  - Localhost-only bind plus a trusted-host guard (foreign Host header gets 400); GET routes only, so the viewer can never change state or spend money.
  - Hardening beyond the plan: best-effort failed-mark in the pipeline; view-model guards for corrupt state, PAID_RECORD, URL quoting; security headers (nosniff, CSP, no-referrer), no-store caching, NUL rejection in paths.
  - viewer.js does not reload while media plays; shows a "New results are ready - Refresh" banner. Step bar follows on phones.
  - CLI catches uvicorn's SystemExit on bind failure (exit 2) and detects wildcard listeners via connect_ex.
  - Browser opens after a fixed 1 s delay (readiness check is a backlog item).
- **Not done:** live prepare check and the manual browser checklist are Frank's steps.

## 2026-11-02 — Session 2: viewer sign-off, Replicate connected, Plan 2 order
- **Goal:** Get ready for the first real (paid) prep run and decide how Plan 2 starts.
- **Completed:** Viewer review fixes pushed to main (400 tests, 97.08 % coverage; approve command hidden on failed jobs, home path shown as `~`, port bind race). Frank clicked through the demo jobs and approved the viewer ("looks good"). Viewer stopped. `.env` replaced with the project's own template (chmod 600, git-ignored). Frank created his Replicate account and put the token in `.env` himself; `charswap doctor` shows REPLICATE_API_TOKEN set, PIAPI_API_KEY missing, hosting = tmpfiles.
- **Decisions:**
  - Proposed: Plan 2 builds Wan 2.2 Animate (Replicate) first, Seedance 2.5 (PiAPI) second. Why: Wan only needs the Replicate account Frank already has, so the first end-to-end swap comes days sooner; Seedance needs a PiAPI Creator plan (~$15/month) and face-library setup before anything works. Seedance stays the 1080p "YouTube quality" path. Frank hasn't objected yet.
- **Lessons:** Frank expected "give a video, get my face" to work already. Say plainly which half (disguise vs generate) exists when reporting status. Don't leave local servers running; stop them after his check.
- **Next:** Frank picks a test clip (under 15 s, one person talking). Run a live prep (~$0.06) only after he confirms, and watch it in the viewer. Then start Plan 2 (prerequisites in scratchpad first).

## 2026-11-03 — Session 3: Plan 2a (Wan generation) built
- **Goal:** First end-to-end swap path: Wan 2.2 Animate on Replicate, draft then final, original soundtrack restored.
- **Completed:** `charswap prepare ... --generator wan --mode direct --character <photo> [--language]` and `charswap generate <id> --draft|--final [--yes]` with cost confirmation; `status` shows `next`. Generator interface and Wan generator, one mode table (`modes.py`), neutral provider errors, spend log with provider/model/task id, 429 create retry, polling backoff 5 to 30 s, empty-input guard, soundtrack pre-flight and atomic original-audio restore, stale draft/final remake (old files kept as draft-old-*/final-old-*), viewer Draft/Final sections with Generating / Draft ready / Finished / Out of date and slow-polling of failed/waiting/stale jobs, Whisper fixes (condition_on_previous_text false, language allowlist checked before the cost question). 571 tests, 97.57 % coverage, ruff clean; merged to main and pushed after the final whole-branch review ("ready to merge"). README has a "Try it with Wan" section.
- **Decisions:**
  - Wan first. Why: it only needs the Replicate account Frank already has; Seedance needs a PiAPI plan and face-library setup.
  - Colour depth kept, grey deferred. Why: colour is the proven input; grey only if results look wrong.
  - 429 on create is retried (exception to "never retry a paid request"). Why safe: Replicate throttles before creating anything, so nothing exists or is billed; the run releases its "creating" marker, waits, and re-claims it exclusively, so two runs can never both POST.
  - The draft stores the review fingerprint and the final is refused unless it matches. Why: ties the final to the draft Frank actually watched.
  - Hosting moved to 2b. Why: Wan uses Replicate's own file upload; only PiAPI needs public URLs.
- **Lessons:**
  - Low-balance Replicate accounts are rate-limited (~6 requests/min); fixed 5 s polling starved the next create.
  - Whisper on Replicate needs condition_on_previous_text false for rap/fast speech, or it returns a few words.
  - Reviewers found double-charge paths again, so keep adversarial money reviews on every paid change.
- **Not done:** live Wan test (Task 15, needs Frank's OK); Wan price still unconfirmed ($0.02/s 480p, $0.05/s 720p are estimates). Leftovers are in docs/scratchpad.md "Deferred from Plan 2a".

## 2026-11-04 — Session 4: Plan 2a shipped, first live Wan draft
- **Goal:** Merge Plan 2a and run the first paid Wan test.
- **Completed:** feat/plan-2a-wan merged to main and pushed (571 tests, 97.57 % coverage). Compared our project with the original reference: the prep half is the same technique; the difference is the generator (reference uses Seedance, which with SAM `--subject` can target one of two people; Wan picks one person itself). Test clip: a 12 s one-person talking-head clip from YouTube (git-ignored `inputs/talking-head-12s.mp4`). Frank's first three photos were ~200 px screenshots (too small); he supplied an 828x1086 original. Free prepare → approved → paid 480p draft (prediction d76cxwh659rmt0d1mwasfaz1am): ~1 min 45 s, 832x464 at 30 fps, original audio restored at 12.00 s. Identity clearly Frank's.
- **Decisions:**
  - Quick Wan test before Plan 2b. Why: proves the whole chain for ~$0.24; Seedance stays the target for two-person clips like the rap clip.
  - Did not use the two-person rap clip with Wan. Why: Wan can't be told which person to replace, and cuts plus sunglasses would make a failed test uninformative.
- **Lessons:**
  - Wan copies everything in the character photo: a shirtless face-only selfie produced an invented tank top, and the photo's background (a building) appeared as a window in the scene. Ask for head-and-shoulders, clothed, plain-background photos.
  - Screenshots of photos are too small (~200 px); ask for the exported original.
  - Real run was ~1 min 45 s, much faster than the 3–8 min guess.
- **Next:** Frank checks the real charge on Replicate billing (update prices); optionally retake the photo and redo the draft, then `--final`. Then Plan 2b (Seedance via PiAPI, two-person clips).
