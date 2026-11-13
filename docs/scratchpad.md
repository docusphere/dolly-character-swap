# Scratchpad

## Active work
- [x] S1 — Plan 1: foundation + preparation pipeline (239 tests, 96.3 % coverage; branch feat/plan-1-foundation)
- [x] Job viewer (read-only, `charswap view`; 389 tests, 97 % coverage; branch feat/job-viewer)
- [x] S2 — Replicate token set by Frank; `doctor` passes for prep (PIAPI_API_KEY still missing)
- [x] S2 — First live prep on a real clip (COLORS 15 s, two people, depth mode): 9/9 steps, ~$0.06, job c0fcbc10…
- [x] S3 — Plan 2a — Wan generation (draft/final, original audio), live-run fixes, prerequisites (571 tests, 97.6 % coverage; merged to main)
- [x] S4 — Live Wan draft (Task 15): 12 s talking-head clip + Frank's selfie → 480p draft in ~1 min 45 s, identity carried over, original audio restored (job 09b020f5…)
- [ ] Confirm the real Wan charge on replicate.com billing (estimate ~$0.24 for 12 s at 480p) and update `costs.py` prices
- [ ] Optional: retake character photo (head and shoulders, clothed, plain wall) → new draft, then `--final` 720p (~$0.60)
- [x] S5 — Plan 2b — PiAPI Seedance 2.5 + face library, PiAPI upload (replaces R2/tmpfiles), two-person clips (`--subject`, @image1/@image2 slots), prompt review (751 tests, 96.5 % coverage; branch feat/plan-2b-seedance)
- [ ] Live Seedance test (Task 14, needs Frank's OK + PiAPI Creator plan)
- [x] WaveSpeed Seedance generator (default provider; 968 tests, 96.92 % coverage; branch feat/wavespeed)
- [ ] Live WaveSpeed draft through charswap (Task 14 of the WaveSpeed plan, ~$0.88, needs Frank's OK)
- [ ] Plan 3 — web UI, skill, docs, legacy removal

## Do first in Plan 2 (from final Plan 1 review)
- [x] S5 — (2b) Decide whole-second clip length: PiAPI `duration` is an int 4–30, `normalize` keeps fractional seconds, and `restore_original_audio` refuses > 0.25 s longer / > 0.5 s shorter. Snap video+audio to whole seconds at normalize (and use that as the soundtrack source) or round up and trim the generated video. Add a normalize → restore test with an integer-length generated video.
- [x] Persist normalize metadata in state.json (seconds, width, height, frames, aspect ratio); real-length cost estimates instead of fixed 30 s.
- [x] Spend log: one entry per paid call (provider, model, task id, units, estimate) written when the paid request is created; don't charge depth when provider is Hugging Face. (done for generation in S3; prep still logs one 'prepare' entry; HF depth still counted, Plan 3)
- [x] `providers/errors.py` (ProviderError, OutputExpired) + app-wide `CharSwapError` base; drop class-name matching in `stages/__init__.py`; catch prompt `ValueError` in CLI.
- [x] Single `modes.py` table (mode → generator, paid stages, review files, uses_depth); generator limits come from generator classes; stage error messages read prices from costs.
- [ ] (Plan 3, decision P10) Rename `original.mp4` → `normalized.mp4`; depth_mesh composite writes `depth-composite-silent.mp4` directly (remove the copy trick).
- [x] Stage status "running"/"failed" in state (done with the viewer)
- [ ] (Plan 3, decision P11) Per-job file lock (or documented single-writer rule).
- [x] (Viewer final review) `pipeline.py` owns stage order: `prepare()` loops over `PREP_STAGES_BY_MODE`; keep generation stages (draft/final) in a separate group so "active"/"Preparing" doesn't treat every prepared-but-not-generated job as running.
- [ ] (Viewer final review) Move SECTIONS + "Why this step?" copy to `web/sections.py` with a per-section `texts` hook (remove id-string branching in view_model/job.html); generation sections plug in there.
- [x] Review gate requires every prep stage done (status "unavailable", decide refuses) — done in viewer fixes.

## Found in the first live run (S2) → Plan 2a
- [x] Rate limits: low-balance Replicate accounts get ~6 req/min; polling every 5 s burns it and the next create gets 429. Poll slower (backoff), and a 429 on create means nothing was created → wait (Retry-After) and retry safely.
- [x] Whisper on Replicate returned only "Let's get it." for rap; same vocals locally with language=en + condition_on_previous_text=False got the lyrics. Pass those inputs.
- [x] Viewer: failed jobs stop polling, so a resume isn't shown live → slow-poll failed jobs.
- [ ] SAM leaked a real arm for a few frames (arm crossing body) — repair (RGB) exists; consider in the review checklist.

## Deferred from Plan 2a
From the plan's Deferred table:
- [x] S5 — (2b) File hosting: replaced by PiAPI's own temporary upload (R2 and tmpfiles dropped)
- [x] S5 — (2b) PiAPI Seedance 2.5 generator, draft to 1080p promote (or 720p), face library (`charswap face check`), `generates=True` on Seedance modes
- [x] S5 — (2b) Whole-second clip length (PiAPI `duration` is an int)
- [x] S5 — (2b) Prompt review before a Seedance draft (`charswap prompt`)
- [ ] (2b if needed) Persist the full `GenerationRequest` in state.json
- [ ] (Plan 3) Rename `original.mp4` to `normalized.mp4` (P10)
- [ ] (Plan 3) Per-job file lock / run lock with heartbeat (P11)
- [ ] (Plan 3) Per-call spend entries for prep stages; don't count depth when it ran on Hugging Face
- [ ] (Plan 3) `web/sections.py` with per-section `texts` hook (remove id branching)
- [ ] (Plan 3, UI) Hashed approval of the draft before the final (P4)
- [ ] (Plan 3) Wan prep without `REPLICATE_API_TOKEN`, shared `services.py`, close `httpx.Client`
- [ ] (Plan 3) Re-encode to AAC fallback when upload audio can't be stream-copied into mp4 (e.g. PCM in .mov)
- [ ] (Plan 3) depth_mesh writes `depth-composite-silent.mp4` directly; stage messages read prices from `costs`
- [ ] (Plan 3) SAM arm-leak item in the review checklist (Seedance depth modes only)
- [x] S3 — Owner uuid in the "creating" marker so a 429 retry only removes its own marker (commit 661a3ad)
- [ ] (Plan 3, with run lock) A run that loses the 429 re-claim race marks the stage "failed" while the winner still runs (cosmetic, no money risk)
- [ ] (Backlog) `charswap cancel <job>` for a running prediction

Review leftovers:
- [ ] Owner check before `_save` after a 201 create
- [ ] A resumed draft records its fingerprint at resume time (should be at create time)
- [ ] Legacy jobs from before fingerprints count as stale
- [ ] iPhone .mov audio start offset (`start_time`) vs generated video starting at 0
- [ ] Spend-log rewrite is not cross-process safe
- [ ] Partial archive failure reporting (draft-old-*/final-old-*)
- [ ] Mode names still hardcoded in a few places (cli.py repair check, view_model, pipeline)
- [ ] Ctrl-C at the cost question prints "not charged again"

## Deferred from Plan 2b
From the plan's Deferred table:
- [ ] `reference` task type (if the live test shows `edit` is wrong): follow-up task after Task 14
- [ ] BytePlus ModelArk generator (ByteDance direct, cheaper; needs Frank's verified business account + real-person access): own small plan once BytePlus confirms
- [ ] Use `seed` / `audio` if PiAPI documents them for 2.5: after the live test
- [ ] Seedance 2.0 fallback (15 s, no promote): only if 2.5 refuses faces
- [ ] (Plan 3, `services.py`) `build_services` without `REPLICATE_API_TOKEN` (a Seedance-only machine)
- [ ] (Plan 3) Delete PiAPI temporary uploads / assets from the CLI (`charswap face remove`)
- [ ] (Plan 3 UI) Viewer: face library panel, prompt editing in the browser
- [ ] (Backlog, with `charswap cancel`) PiAPI task cancel

## Deferred from the WaveSpeed plan
- [ ] Two-person clips on WaveSpeed (W6): after a live PiAPI two-person test
- [ ] WaveSpeed webhooks or sync mode (backlog)
- [ ] Refuse a too-large upload before the cost question (today it happens inside `generate`, after the yes, before any upload or POST): Plan 3 (`services.py` pre-flight)
- [ ] Viewer: show the provider in the job header (Plan 3 UI)
- [ ] Project SKILL file (Plan 3)
- [ ] BytePlus generator: own plan, if ever
- [ ] Record WaveSpeed's real low-balance answer code at the first live run, so it can move to the clean-refusal list (P5)
- [ ] Some free refusals inside the generator (upload too large, missing photo, refused upload ticket) happen after a remake has already moved the old draft/final to *-old-*; nothing is charged, but the job shows no draft until the next run.
- [ ] Second final take at another resolution (offer to move the current final to final-old-* and make a new one). Today `--final --resolution X` after a done final at another resolution is refused (final review).

## Backlog (decided S2)
- Grey depth option (`--depth-color grey`): clearer on orange/warm backgrounds; colour kept as default because it is the proven input. Try only if results look wrong. A/B by eye in the viewer; pose-match score later.

## Before Plan 3 (UI + skill)
- Upload audio that cannot be stream-copied into mp4: re-encode to AAC fallback (see Deferred)
- Hashed draft approval before the final (2a uses the cost confirmation)
- Stat-keyed fingerprint cache for `review.status()` polling (full hash stays on approve/generate).
- Move `build_services` to shared `services.py`; Wan path must not require REPLICATE_API_TOKEN for prep; close `httpx.Client`.
- Move HF depth client + face model download into `providers/`.
- SKILL.md rule: agent never runs `review --approve` without the human's explicit approval.
- Validate character image with PIL at job create; reject silent clips early; validate mode/generator before the cost question.
- `doctor`: required vs optional checks, HF_TOKEN, face model present.
- Symlink rejection in fingerprinting; per-job "running" lock for prepare vs decide.
- CSRF for any state-changing route: Origin / Sec-Fetch-Site same-origin check + per-session token or custom header; never a GET that spends money.
- Cross-process run lock per job (lock file with pid + heartbeat) → real "is it running?" answer; background threads never get KeyboardInterrupt.
- Cost confirmation dialog before every paid button (mirror CLI confirm).
- Viewer polish: slow-poll (~10 s) failed jobs so `charswap resume` shows up live; repair SAM cost row; check nav scroll on phone width before recording; direct mode shows original.mp4 twice.

## Before charging users (platform)
- Decimal / micro-USD everywhere; log-before-call semantics; partial-spend logging for abandoned jobs.
- Consent/ToS/EU AI Act deepfake labeling; charge rejected runs (Higgsfield-style).

## Backlog
- Stale "running" after a hard kill (SIGKILL/power loss): needs worker heartbeat/pid (Plan 3)
- Viewer: browser open uses a fixed 1 s delay; switch to a readiness check
- Viewer: macOS wildcard-listener detection fixed (connect_ex); nothing to do
- BytePlus direct provider (if enterprise access granted)
- Hosted demo inside Dolly (separate spec) → unified API + MCP + credits
- Multi-person face mesh
- Input-hash check on reused prediction records; `--regenerate <stage>`
- Compact landmark JSON; pinned face model URL + SHA-256
