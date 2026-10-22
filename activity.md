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
