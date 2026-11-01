# Scratchpad

## Active work
- [x] S1 — Plan 1: foundation + preparation pipeline (239 tests, 96.3 % coverage; branch feat/plan-1-foundation)
- [x] Job viewer (read-only, `charswap view`; 389 tests, 97 % coverage; branch feat/job-viewer)
- [ ] Plan 2 — generation (hosting, PiAPI, Wan, face check, audio restore)
- [ ] Plan 3 — web UI, skill, docs, legacy removal

## Do first in Plan 2 (from final Plan 1 review)
- [ ] Decide whole-second clip length: PiAPI `duration` is an int 4–30, `normalize` keeps fractional seconds, and `restore_original_audio` refuses > 0.25 s longer / > 0.5 s shorter. Snap video+audio to whole seconds at normalize (and use that as the soundtrack source) or round up and trim the generated video. Add a normalize → restore test with an integer-length generated video.
- [ ] Persist normalize metadata in state.json (seconds, width, height, frames, aspect ratio); real-length cost estimates instead of fixed 30 s.
- [ ] Spend log: one entry per paid call (provider, model, task id, units, estimate) written when the paid request is created; don't charge depth when provider is Hugging Face.
- [ ] `providers/errors.py` (ProviderError, OutputExpired) + app-wide `CharSwapError` base; drop class-name matching in `stages/__init__.py`; catch prompt `ValueError` in CLI.
- [ ] Single `modes.py` table (mode → generator, paid stages, review files, uses_depth); generator limits come from generator classes; stage error messages read prices from costs.
- [ ] Rename `original.mp4` → `normalized.mp4`; depth_mesh composite writes `depth-composite-silent.mp4` directly (remove the copy trick).
- [x] Stage status "running"/"failed" in state (done with the viewer)
- [ ] Per-job file lock (or documented single-writer rule).

## Before Plan 3 (UI + skill)
- Stat-keyed fingerprint cache for `review.status()` polling (full hash stays on approve/generate).
- Move `build_services` to shared `services.py`; Wan path must not require REPLICATE_API_TOKEN for prep; close `httpx.Client`.
- Move HF depth client + face model download into `providers/`.
- SKILL.md rule: agent never runs `review --approve` without the human's explicit approval.
- Validate character image with PIL at job create; reject silent clips early; validate mode/generator before the cost question.
- `doctor`: required vs optional checks, HF_TOKEN, face model present.
- Symlink rejection in fingerprinting; per-job "running" lock for prepare vs decide.

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
