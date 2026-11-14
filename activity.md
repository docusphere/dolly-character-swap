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

## 2026-11-08 — Session 5: Plan 2b (Seedance 2.5 on PiAPI) built
- **Goal:** Second generator: Seedance 2.5 on PiAPI with real faces, a cheap 480p draft and a 1080p or 720p final, for one- and two-person clips.
- **Completed:** On branch feat/plan-2b-seedance (not merged yet).
  - PiAPI client: free calls (temporary upload, face assets, download) and paid tasks that are created once, saved before waiting and resumed, never re-bought. Shared money-safe record helpers now serve both the Replicate and PiAPI clients.
  - Face library in `faces/` (git-ignored) with `charswap face add|list|check|renew` (all free).
  - Characters are chosen at `prepare` for both generators: `--face`/`--character`, plus `--face2`/`--character2` for a second person on Seedance. The review fingerprint covers them.
  - Whole-second clips for Seedance: `normalize` drops the tail and the original sound is cut to the same length.
  - Upload copy: a `prepared.mp4` over 7 MB is re-encoded smaller with the audio packets copied untouched; still too big means a refusal before paying.
  - Seedance generator: 480p draft, then a 1080p promote of that draft (default) or a fresh 720p run (`--resolution 720p`). Estimates round up to whole seconds and include the input-video charge.
  - `charswap prompt <id>` writes and prints `prompt.txt`; `generate --draft` shows the full prompt before the cost question, and the text sent is saved with the draft.
  - CLI and doctor: `PIAPI_API_KEY` is needed for the face commands and Seedance generation (Wan never asks for it); `doctor` reports it.
  - Viewer shows the Seedance draft and final with the prompt, resolution and PiAPI costs.
  - 751 tests, 96.50 % coverage, ruff clean. README has a "Try it with Seedance" section.
- **Decisions:**
  - Q1 PiAPI first, BytePlus later as one more generator file. Why: PiAPI is the only documented real-face path on Seedance 2.5 today; BytePlus verification is pending.
  - Q2 Hosting is PiAPI's own 24 h upload; R2 and tmpfiles dropped. Why: the Creator plan needed for faces already includes it, so no Cloudflare account.
  - Q3 Characters chosen at `prepare` and hashed by the review. Why: one rule for both generators, and the final can only use what was reviewed.
  - Q4 Face library in `faces/`, jobs store the face name and the asset id is looked up and checked `Active` before any charge. Why: free calls only, and `renew` re-uploads the same photo so the identity never silently changes.
  - Q5 Whole-second clips with the sound cut to match. Why: PiAPI durations are whole seconds and the sound must stay in sync.
  - Q6 Task type `edit` as one constant. Why: "replace the person in @video1" is an edit; the live test confirms it, and switching to `reference` is a small follow-up.
  - Q7 Final is a 1080p promote (default) or a fresh 720p run. Why: 1080p is an exact upgrade of the watched draft (~$16 for 12 s); 720p is ~$7 but can differ a little.
  - Q8 Prompt review through `prompt.txt`, outside the review fingerprint. Why: the confirmed text is what is sent, and editing it never clears the review.
  - Q9 Upload copy under 7 MB with untouched audio. Why: PiAPI's upload limit is 10 MB and the pitched vocals must survive for lip sync.
  - Q10 A PiAPI 429 on a paid create is not retried. Why: the 429 exception covers Replicate only; the rule stays simple for a new provider.
  - Q11 A task that fails after creation keeps its record. Why: never re-buy without the user's yes; PiAPI refunds content rejections.
  - Q12 No paid "face check" generation. Why: the library's `Active` status is the check and the first draft is the real test.
- **Lessons:**
  - The preview shown before a stale-draft remake must be built from the request that will really be sent (`replacing_stale`), not from the old paid draft.
  - A failed 720p attempt must not become the default: a plain `--final` afterwards is a 1080p promote again.
  - A promote with no saved draft task id is refused before the cost question, not after the user says yes.
  - Resuming a paid generation must never depend on today's `prompt.txt` or face library; it uses what was saved at create time.
  - Estimates round seconds up so they only ever over-quote.
- **Not done:** live Seedance test (Task 14, needs Frank's OK and a PiAPI Creator plan). Leftovers are in docs/scratchpad.md "Deferred from Plan 2b".

## 2026-11-13 — Session 6: WaveSpeed Seedance generator (default provider)
- **Goal:** Make a viewer-friendly path: sign up at WaveSpeed ($1 free credit, pay per use), `prepare --character me.jpg`, a 480p draft, then a new 720p or 1080p take. PiAPI stays as an option.
- **Completed:** On branch feat/wavespeed (not merged yet).
  - WaveSpeed client (`providers/wavespeed.py`): free upload (ticket, then PUT), one paid create that is never retried and is guarded by the exclusive "creating" marker, prediction id saved before waiting, polling that backs off from 5 s to 15 s, WaveSpeed errors turned into plain messages ("WaveSpeed answered <code>: <excerpt>"). Shared `download_now` for both providers.
  - `SeedanceWaveSpeedGenerator`: free checks first (prompt names, missing photo) before any upload, 480p draft, 720p or 1080p final as a new take.
  - Provider per job: `provider` in `state.json` is set at prepare and pinned before any paid create; old jobs are resolved by `pipeline.generator_key`. Finals and "upgrade the draft" come from generator attributes (`finals`, `promotes`), not from `generator == "seedance"`.
  - Prompts use the provider's input names (`@Video 1` / `@Image 1` for WaveSpeed, `@video1` / `@image1` / `@image2` for PiAPI), with a digit-boundary check; an old PiAPI-style `prompt.txt` is renamed for WaveSpeed jobs.
  - `prepare --provider wavespeed|piapi` (default WaveSpeed), default mode `depth_mesh`, rights question for real faces (`--yes` answers it and the cost question), and prepare asks for the chosen provider's key first. WaveSpeed refuses `--face` and two people.
  - `doctor` reports all three keys and both uploads; `.env.example` has `WAVESPEED_API_KEY`; the viewer names the provider on each job and still never writes.
  - 968 tests, 96.92 % coverage, ruff clean (the branch started at 780 tests).
- **Decisions:**
  - W1 WaveSpeed is the default Seedance provider. Why: easiest sign-up for viewers, and no finished PiAPI work is thrown away.
  - W2 The provider is chosen at `prepare` and saved on the job. Why: a PiAPI draft upgrade needs a PiAPI draft, and faces differ at prepare time.
  - W3 The generator stays `seedance`; `provider` is a separate field. Why: prep, prompts and whole-second rules stay shared; only PiAPI behaviour sits behind a flag.
  - W4 Default mode is `depth_mesh`. Why: much better lip sync than `depth` in the live test.
  - W5 WaveSpeed finals are fresh runs (720p, or 1080p on request). Why: WaveSpeed has no draft upgrade and no seed.
  - W6 Two-person clips stay PiAPI-only. Why: the PiAPI two-person path is still untested live.
  - P1 `generator_key`, `pin_provider` and `seedance_provider` live in `pipeline.py`. Why: the old-job rule reads the job's record files.
  - P2 `GENERATOR_CLASSES` maps each key to its class. Why: prices, finals and the viewer work without API keys.
  - P3 "Promote" means a generator with `promotes = True` making its default final. Why: that is exactly PiAPI's 1080p upgrade; WaveSpeed never promotes.
  - P4 The placeholder check moved to `prompts.check_placeholders`. Why: the generator runs the same free check before uploading.
  - P5 Only 400/401/403/404/422 and 429 release the paid-create marker; any other answer keeps it. Why: an unclear result must never allow a second paid create. A refusal that names a prediction id saves it.
  - P6 Polling backs off 5 s to 15 s, at most 720 polls (about 3 hours). Why: a draft takes about 3 minutes; then "still running, no new charge".
  - P7 The rights question is provider-neutral and runs before the cost question. Why: `--yes` answers both, and PiAPI keeps its own wording.
  - P8 An unknown `provider` value is an error, never guessed. Why: fail fast on damaged state.
  - P9 The viewer labels the prompt line and cost rows with the provider's name. Why: no PiAPI or "upgrades the exact draft" wording on a WaveSpeed job.
  - P10 `--character` photos still go through the free `faces.check_photo`, with provider-neutral wording. Why: refuse a bad photo before any question.
- **Lessons:**
  - Gate PiAPI-only behaviour on generator attributes, not on `generator == "seedance"`; the plan's grep found only generator-level checks left.
  - An unclear answer to a paid create keeps the marker; only clear refusals release it.
  - A placeholder check by substring needs a digit boundary (`@image1` inside `@image12`).
  - Check free things (missing photo, prompt names) before the first upload, and a missing key before anything else.
  - Money-path reviews on every task again caught real problems; keep them.
- **Not done:** live WaveSpeed draft through charswap (Task 14, about $0.88, needs Frank's OK); merge or push of feat/wavespeed (needs Frank's OK). Leftovers are in docs/scratchpad.md "Deferred from the WaveSpeed plan".

## 2026-11-13 — Session 7: Live WaveSpeed draft, merge to main
- **Goal:** prove the WaveSpeed flow with one real paid draft, then ship the branches.
- **Completed:**
  - Live draft through `charswap generate --draft` on the Harry 4 s depth_mesh job (ea899aff…): 227 s on WaveSpeed, about $0.88, 854×480, 24 fps, 4.0 s, original sound back on. No depth colours or mesh lines left in the frames.
  - The tool stopped first with "Review required" (the gate works) and renamed the old `prompt.txt` names to WaveSpeed's on its own.
  - Added `tests/test_live_wavespeed.py` (marked live, not in the default run).
  - Merged feat/plan-2b-seedance and feat/wavespeed into main and pushed.
- **Decisions:**
  - Frank: paid tests up to about $5 run without asking; ask above that or for runs at the same time. Why: small test charges are not worth an interruption.
- **Lessons:**
  - A job prepared before the review gate was used still needs `charswap review --approve` before any paid step.

## 2026-11-14 — Session 8: Walkthrough app (Plan 3a, visual part)
- **Goal:** a local page to walk YouTube viewers through how a swap is made.
- **Completed:**
  - Spec and plan for the click-through app (14 tasks), each reviewed; the plan review caught a paid button without a price dialog (fixed in the plan).
  - Built Tasks 1–4: `web/steps.py` (all texts), the five step screens and home page (`web/pages.py`), the before/after wipe player (`static/app.js`), `charswap app` (`view` stays as an alias).
  - Browser checkpoint on the real job ea899aff…: every tab, synced playback, divider, phone width, dark mode, no console errors.
  - Review fixes: step 2 opens on the newest result ("What the AI gets"), a paused clip blocks auto-reload, a labelled "Compare" divider, clip and photo at one size, 404s for odd file names, dead viewer code removed.
  - Removed the New swap button (no upload yet); README gets a walkthrough section.
- **Decisions:**
  - Stop at the read-only walkthrough; Tasks 6–14 (buttons) are parked. Why: Frank only needs to show the steps on camera, and swaps are made with the CLI.
  - No final for the demo job; the walkthrough ends at the draft. Why: Frank does not need it for the video.
- **Lessons:**
  - Picking the default tab by page order showed the least visual tab (Voice); order by pipeline stage instead.
