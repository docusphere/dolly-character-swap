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
