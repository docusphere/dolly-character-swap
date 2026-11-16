# Scene swap: swap one person in one shot of a longer scene

Status: spec review passed after three rounds, 2026-11-14.
Builds on: `2026-11-12-wavespeed-seedance-design.md` and `2026-11-13-click-through-app-design.md`.
Branch: `feat/scene-swap`, created from `main` (9c47cc4).

## Goal

Frank has a 17 s film scene with three shots: police (0–2.63 s), a close-up of the man in
white face paint (2.63–15.43 s), and a masked man (15.43–17 s). He wants only the man in face
paint to become him. Every other pixel and the whole soundtrack must stay the original.

Today `charswap` sends the whole clip to Seedance, which redraws every frame of every shot.
This feature sends only the chosen shot (and inside it, only the hidden person is used), then
lays the AI's person back over the untouched scene.

Test clip: `inputs/dark-knight-1m38-1m55.mp4` (1920×1080 with black bars, real picture
1920×800 at y = 140; 23.976 fps, 408 frames; 5.1 AAC sound, 17.0 s). Photo:
`inputs/frank-swap-photo.jpg`.

## Decisions

| # | Decision | Why |
|---|---|---|
| S1 | The user names the part with `--from S --to S` (seconds). Each edge snaps to a hard shot cut found within 0.3 s of it. If there is none, the time is used as typed (to the nearest frame), and the output says so. No `--shot N`. | Frank sees times in his editor. Snapping fixes the frame-exact problem: `--from 2.6` alone would include one police frame. A numbered-shot option adds more to explain and test, for no gain here. |
| S2 | All of this happens at `prepare`, before any question and before the job exists. The chosen part, padded, becomes the job's `upload.mp4`. The full scene is kept as `full-scene.mp4`. The existing prep stages run unchanged on `upload.mp4`. | It reuses the whole tested pipeline (normalize, Demucs, Whisper, depth, SAM, mesh, review, costs). Vocals and transcript come from the part only, for free. Nothing else in the scene goes to any AI. |
| S3 | The part is padded to whole seconds (at least 4) by holding its last frame. The sound is padded with silence. It is written at 24 fps with exactly N × 24 frames, so `normalize` keeps every frame. | Seedance takes whole seconds only. A held frame never shows another shot. Writing exactly N × 24 frames avoids the existing whole-seconds tail cut: a 13 s part written at 23.976 fps can measure 12.99 s, and `normalize` would cut it to 12 s, dropping 0.8 s of real frames. |
| S4 | Black bars are found automatically and left out of what the AI sees. A bar is an edge row or column that is near-black (max ≤ 24 of 255) in every frame of the part. There is no flag. | The AI then sees the 1920×800 picture, not 1080 rows with 280 black ones. That gives about 35 % more detail on the face at 480p. A row counts as a bar only when nothing ever enters it, so a dark scene loses nothing. |
| S5 | In a scene job, SAM 3 cuts out `--subject` on the original colors, not on the depth video. | A description like "man with white face paint" can't be seen in a depth map. `repair` already runs SAM on the colors for the same reason. Single-shot jobs keep using depth. |
| S6 | One-person check (scene jobs): at the end of the `composite` stage (so `mask.mp4` exists to look at), prepare stops before face mesh and review when, in more than 10 % of frames, the mask has a second separate region at least 25 % the size of the largest one. The message names `--subject` and what a new job costs (about $0.06). | One photo means one person to hide. With `--subject "person"`, the masked man's shoulder in the foreground would be hidden too, and the AI could put Frank's face on it. |
| S7 | Paste-back is part of the draft and final steps, not a new command. The paid step makes `draft.mp4` (or `final.mp4`): the full scene at its own size, frame rate and soundtrack. | One command and one file to watch, as today. Resume and never-pay-twice keep working through the same step records. |
| S8 | The pasted region per frame is `dilate(A ∪ (B ∩ near(A)))`, with a feathered edge. A = the original subject mask. B = SAM 3 `"person"` on the AI result. `near(A)` = A grown by 4 % of the picture height. | B catches a hairline or outline that is bigger than the original person. `--subject` is not used on the result, because Frank has no face paint. `∩ near(A)` keeps out anyone else the AI redrew, such as the shoulder. |
| S9 | The paste-back works on `full-scene.mp4`'s own frames (1920×1080, 23.976 fps), not on the 960 px, 24 fps job copy. The AI picture is scaled up to the crop box. | Working on the job copy would shrink the whole scene to 960 px. Bars stay untouched because the blend never reaches outside the crop box. |
| S10 | The output is re-encoded once (H.264, CRF 16). Frames outside the region are decoded and written back unchanged. The soundtrack packets are copied byte for byte. | A frame-exact cut can't be stream-copied (cuts rarely fall on keyframes). CRF 16 is visually lossless, and YouTube re-encodes anyway. Honest wording: "the original pictures", not "bit-identical". |
| S11 | `--setting "TEXT"` (on `prepare` and `resume`) skips the GPT-4o mini call and uses TEXT as the setting. When GPT-4o mini refuses or fails, prepare stops with a message that names `resume --setting`. | Fail fast: never drop the setting silently. `resume --setting` continues free, without a new job, and keeps the paid Demucs and Whisper results. It works for any Seedance job. |
| S12 | Scene swaps need `--provider wavespeed` (the default) and mode `depth_mesh` (default) or `depth`. Other combinations are refused before any question. | The paste-back needs SAM masks (`face_mesh` and Wan have none). YAGNI: PiAPI is untested live. |
| S13 | Single-shot jobs (no `segment` in `state.json`) take exactly today's code path. Every new branch checks `is_scene_job(job)`. | Existing jobs must not change. The existing tests prove it. |
| S14 | App: minimal. On the Draft and Final steps of a scene job, the "before" side is `full-scene.mp4`. Step 1 shows the full scene. Add one line of text. Added after the first live draft: a "Swapped area" tab (the scene with the pasted region tinted) and `charswap edge <job> tight|normal|wide` (free re-lay). | Enough to show "same scene, new face" on camera; the tab shows viewers exactly what was replaced, and the free re-lay handles loose hair without a new paid draft. |

## Flow (the Dark Knight clip)

```
prepare inputs/dark-knight-1m38-1m55.mp4 --from 2.6 --to 15.4 \
        --subject "man with white face paint" --character inputs/frank-swap-photo.jpg
  free, before any question:
    find shot cuts -> 2.628 s (frame 63), 15.432 s (frame 370)
    snap 2.6 -> 2.628, 15.4 -> 15.432; part = frames 63-369 = 12.80 s -> padded to 13 s
    find bars -> crop 1920x800 at (0, 140)
    check the full scene's sound and picture differ by at most 0.25 s (MAX_MISMATCH)
    print the summary, then the rights question, then the prep cost question
  Job.create: full-scene.mp4 = the scene (copied as-is); upload.mp4 = the padded, cropped part
  existing stages on upload.mp4: normalize (960x400, 312 frames) -> Demucs -> pitch ->
    Whisper -> describe (or --setting) -> depth -> SAM 3 on colors -> composite +
    one-person check -> face mesh -> embed
review (unchanged: the hashed files are what the AI sees, padding included)
generate --draft   (the price is for 13 s; asks first)
  Seedance draft on WaveSpeed -> draft-raw.mp4 (padded part, about 1152x480 expected)
  SAM 3 "person" on draft-raw.mp4 -> draft-sam/
  paste back onto full-scene.mp4 frames 63-369; all other frames as they are
  copy full-scene.mp4's sound -> draft.mp4 (17.0 s, 1920x1080, 23.976 fps)
generate --final [--resolution 1080p]   (same paste-back, final-sam/, final.mp4)
```

Summary text at prepare (plain English, example):

```
Scene: 17.0 s, 1920x1080. Shot cuts found at 2.63 s and 15.43 s.
Swapping 2.63 s to 15.43 s (12.8 s; moved to the shot cuts you were near).
The AI only gets this part, made 13 s long by holding its last frame. Black bars are left
out, so it sees 1920x800. The rest of the scene is kept as it is and is never sent anywhere.
```

## File and stage changes

- **`media/scene.py` (new, local, free):**
  - `find_cuts(path) -> list[float]`. Compare each frame with the one before (grayscale, 64 px wide, mean absolute difference). A cut is a difference at least 4 times the median of the 24 frames around it, and at least 12 of 255.
    - Times are frame timestamps, so a variable frame rate still works.
    - Check once by hand on the test clip: the cuts must be frames 63 and 370.
  - `snap(t, cuts, window=0.3)`.
  - `find_bars(path, start, end) -> (x, y, w, h)`: even numbers, at least 300 px each way. Also used on the AI clip in the paste-back.
  - `cut_segment(scene, start, end, crop, dst)`:
    - writes 24 fps, exactly N × 24 frames, each job frame k showing `src(k)` = the latest source frame at or before k/24 (the rule `normalize` uses);
    - saves that map as `segment.frame_map` (a list, one source frame index per real job frame), so the paste-back never recomputes it;
    - the last real frame is held to N s; N = ceil(seconds − 1e-6), at least 4;
    - the sound is the scene's audio from `start` to `end` (relative to the audio stream's first packet, as `restore_original_audio` measures it), as stereo AAC, padded with silence to N s.
  - `paste_back(scene, raw, masks_a, masks_b, segment, dst)`. Stream frame by frame (O(1) memory). For source frame i at time t inside the part:
    - job frame `k(i)` = the first k with `src(k) ≥ i` (from `frame_map`; clamped to the last real one). This is the job frame that shows this very source frame. `round((t − start) × 24)` would be one frame late at 23.976 fps: job frame k shows source frame k − 1;
    - mask A frame = `k(i)`; AI frame = `round(k(i) × ai_fps / 24)`, held at its last frame;
    - `find_bars` runs once on `{stage}-raw.mp4`; that same box is cut from every AI frame and every B mask (SAM ran on the raw clip, bars included) before scaling;
    - scale the AI frame (Lanczos) and masks A and B to the crop box;
    - region = dilate(A ∪ (B ∩ near(A))) by 7 % of the crop height, blurred by 2 % (Gaussian) for the alpha (first live draft: 1.5 % left the original's loose hair as a ghost around the new head);
    - blend only inside the crop box.

    Frames outside the part are written as decoded. The video is written at the scene's own rate (a Fraction), CRF 16.
- **`media/video.py`:** `VideoWriter` takes an optional `Fraction` rate and a `crf`. Its defaults (24, 18) stay as they are.
- **`jobs.py`:**
  - `Job.create(..., scene=None, segment=None, setting="")`.
  - With `scene`, it copies the scene to `full-scene.mp4` and the cut part to `upload.mp4`.
  - It saves `segment` = `{start, end, start_frame, end_frame, seconds, padded_seconds, fps: "24000/1001", width, height, crop: {x, y, w, h}, cuts}`. The key `scene` is already the setting text, so this one is called `segment`.
  - It saves `setting` when one is given.
- **`cli.py`:**
  - `prepare`:
    - `--from`/`--to` (dest `start`/`end`; both or neither);
    - `--setting TEXT` (1–300 characters after cleaning);
    - the S12 refusals and the summary come before the rights question;
    - the cost lines leave out "Scene description" when `--setting` is given.
  - `resume --setting TEXT`: saves it when the describe stage is not done; otherwise it refuses ("the setting is already done").
  - `generate`: the price shown is `generation_estimate(..., replacing_stale=remake)`. When Seedance resumes free (`resumes_without_charge`: a saved record or `draft-raw.mp4`) but `{stage}-sam/sam-prediction.json` is missing, it says "The AI part resumes free; laying it back needs one SAM 3 run (~$0.002)" and asks (`--yes` skips). Only when both are saved does it say "resuming (no new charge)" as today.
  - `repair` on a scene job is refused ("this job already cuts out on the original colors").
- **`pipeline.py`:**
  - `is_scene_job(job)`.
  - `_masks` uses `original.mp4` for scene jobs. The one-person check (S6) runs at the end of the `composite` stage, after `mask.mp4` is written; a failure marks `composite` failed, so a resume re-checks the same saved masks for free and stops again. Scene jobs save `mask_source="rgb"`.
  - The describe stage: with `state["setting"]` it saves `scene = setting`, makes no call and records `source: "you"` on the stage.
  - `_check_soundtrack_fits`, for scene jobs, also compares `full-scene.mp4`'s sound with its own picture length (not `clip_seconds`) and refuses a gap over `MAX_MISMATCH` (0.25 s). The test clip's gap is 0.017 s. The same check runs free at prepare.
  - `generation_estimate(job, generator, request, stage, *, replacing_stale=False)` = the generator's estimate plus a `sam3` line for a scene job whose `{stage}-sam` record is missing, or always on a remake (`replacing_stale`: that folder is archived after the question). The CLI question and `_after_draft_hint` use it. `log_charge` keeps logging the generator's estimate only, so SAM is never logged twice.
  - `generate`: for a scene job, after `raw` exists, it runs `paste.lay_back(job, stage, replicate)` instead of `add_original_soundtrack`. This happens inside the same `_tracked(stage)`. It writes `{stage}.mp4` through `_write_atomically`. The SAM spend is logged once, as step `{stage}-paste`, from `on_created` when that prediction is created.
  - `archive_stale_draft` also moves `{stage}-sam/` to `{stage}-old-<stamp>-sam/`, so a new draft never reuses the old result's masks.
- **`stages/masks.py`:** `segment(..., on_created=None)` passes `on_created` to `replicate.run` (existing callers pass nothing). The "no mask_*.png" case raises `NoMasks(StageError)`, so callers can tell it from a damaged zip.
- **`stages/paste.py` (new):** `stages.masks.segment(raw, job.path(f"{stage}-sam"), "person", replicate, on_created=log_paste)`; `NoMasks` alone becomes an empty B (any other error still stops). Then `clean_border_fragments`, then `media.scene.paste_back`, then `restore_original_audio(video, full-scene.mp4, part, fit=False)`, with no `seconds` cut. `fit=False` (a new keyword; the default `True` keeps today's behaviour) never calls `_fit_video`, which would re-encode the scene at a whole-number 24 fps and drop frames; a gap over 0.25 s raises instead (already refused at prepare).
- **`stages/describe.py`:** when the prediction ends `failed` or `canceled` (a refusal such as "User Input Error"), it raises a StageError naming the flag. A "creating" or damaged record keeps today's message.
- **`costs.py`:** `estimate_prepare(mode, seconds, skip=())` lets prepare and resume leave out `describe`. No new prices: the paste-back uses `sam3`.
- **`web/`:**
  - `pages.py`: the "before" side of the Draft and Final steps is `full-scene.mp4` when present, and Step 1 shows it.
  - `steps.py`: `SCENE_LINE = "Only the person you picked is laid back into the scene; everything else is the original."`
  - `view_model.cost_rows`: no describe row when `setting` came from you; a "Lay back (SAM 3)" row from the `{stage}-paste` entries.
- **README:** a short "Swap one shot of a longer scene" section with the command above (no film names).

## Timing, position and honest limits

- **Timing:** each source frame takes the job frame that was made from it (`k(i)`), so the AI and mask A line up with the source exactly when the AI keeps the input's timing. Only when the source runs faster than 24 fps (frames skipped) is the nearest later one used, at most one frame off. The AI's frame rate is read from its file, never assumed.
- **Length:** an AI clip up to 0.5 s shorter than the real part holds its last frame (the same allowance as `FIT_LIMIT`). Shorter than that stops (see Errors).
- **Position:** small shifts of the person are covered by B, the dilation and the feather. A camera move or reframe by the AI is not corrected. It would show as a seam, and the cheap draft is where you see it. Global alignment is out of scope.
- **Sharpness:** a 480p draft is scaled up about 1.7× (480 → 800 rows) inside a sharp 1080p scene, so the face looks softer. A 720p or 1080p final narrows the gap.
- **Edges and foreground:** where something in front (the shoulder) crosses within the dilation band of the person, the AI's version of that band is used.
- **Aspect ratio:** WaveSpeed's handling of a 2.4:1 input is not tested. Before scaling, the paste-back removes any black bars the AI added (`find_bars` on the AI clip). A uniform resize is then undone by scaling to the crop box. If the aspect still differs by more than 5 % (the AI cropped the picture), the paste-back stops; see Errors. The first live draft settles this.

## Errors

All refusals before payment end with "Nothing was charged." and happen before any question.

| When | Message (gist) |
|---|---|
| Only one of `--from`/`--to` | "Use --from and --to together." |
| `from ≥ to`, or outside the clip | "The scene is 17.0 s; pick --from and --to inside it, --from first." |
| Part under 1 s or over 30 s | "The part to swap is X s; it must be 1 to 30 s." |
| Wrong mode, provider or generator | "Swapping one part of a scene needs WaveSpeed and depth_mesh or depth mode." |
| A cut inside the part (more than 0.3 s from both edges) | A warning, not a stop: "There is a shot cut at 9.1 s inside the part; the AI will see two shots. Check it in the app before approving." |
| The full scene's sound and picture differ by more than 0.25 s | "The scene's sound lasts X s but its picture Y s, so the original soundtrack could not be put back. Re-export the scene so both have the same length." |
| One-person check fails | "SAM 3 found more than one person in 31 of 312 frames (watch runs/<id>/mask.mp4: white is what was cut out). Start a new job with --subject naming the one to swap, for example --subject \"man with white face paint\" (preparing again costs about $0.06)." |
| GPT-4o mini refuses the frame | "GPT-4o mini would not describe this setting (it said: User Input Error). Describe it yourself and continue for free: charswap resume <id> --setting \"a dim room with ...\". (Deleting describe-prediction.json and resuming asks it again, about $0.001.)" |
| `--setting` is empty or over 300 characters | "Describe the setting in 1 to 300 characters." |
| The AI clip is more than 0.5 s short, or its aspect (after removing its bars) is more than 5 % off | "The AI clip is W×H, X s; it can't be laid back into the Y s shot. It is kept at draft-raw.mp4; nothing more was charged. A new draft would most likely come back the same, so don't buy one; this needs a fix in the tool." No re-buy path is offered (a new draft needs the inputs or prompt to change, as today). |
| The result's SAM masks don't match its frames | "Delete <job>/draft-sam/sam-prediction.json and run the same command again (~$0.002)." |
| SAM finds nobody on the result (`NoMasks`, or all masks blank) | Not an error. It prints "SAM 3 found nobody on the AI clip; only the original outline is used." and uses B = ∅. A damaged zip keeps today's "delete the record" message. |

A paste-back failure never deletes `draft-raw.mp4`. Running the command again re-uses it and
any saved SAM record, so nothing is paid twice.

## Cost (WaveSpeed $0.11/s at 480p, billed on input + output seconds, at least 4 s)

The price uses the padded part (13 s), never the 17 s scene.

| Step | Dark Knight clip |
|---|---|
| Prepare: Demucs $0.017 + Whisper $0.00075 + GPT-4o mini $0.001 + depth $0.04 + SAM $0.002 | about $0.061 ($0.060 with `--setting`) |
| Draft 480p: (13 + 13) × $0.11 | $2.86 |
| Lay-back SAM on the draft | $0.002 |
| **Through the draft** | **about $2.92** |
| Final 720p: 26 × $0.22 + $0.002 | about $5.72 |
| Final 1080p: 26 × $0.55 + $0.002 | about $14.30 |

The money rules from the WaveSpeed spec stay unchanged: one POST per paid create, the claim
marker first, the id saved before waiting, no auto-retry, every paid action asked first.

## Testing (no paid calls; `tests/fakes.py`; coverage ≥ 85 %; ruff clean)

- **`media/scene.py`** with synthetic clips from `tests/helpers.py`, written at 24000/1001 fps:
  - cuts found at known frames, and snapping from nearby times;
  - no cut nearby → the time is used as typed, with a note;
  - bars found, but not where a bright object ever enters;
  - `cut_segment`: exactly N × 24 frames, held frames equal the last real one, audio N s with trailing silence;
  - `normalize` on its output keeps every frame (this guards S3).
- **`paste_back`:**
  - outside the region and outside the part, pixels match the decoded scene within encoder tolerance;
  - bars stay black;
  - frame count and frame rate equal the scene's;
  - the audio packets are byte-identical to `full-scene.mp4`;
  - a far-away B region (the shoulder) is not pasted;
  - the timing map at 23.976 vs 24 fps, with exact expected indices (source frame 1 → job frame 2, source frame 300 → job frame 301);
  - an AI clip with its own black bars, plus a B mask sitting exactly on its person, lands exactly on A; `fit=False` never calls `_fit_video`;
  - a short AI clip is held or refused, and a wrong aspect is refused.
- **Pipeline:**
  - SAM runs on `original.mp4` for scene jobs and on `depth.mp4` otherwise;
  - the one-person check;
  - `--setting` makes no describe call;
  - a fake failed describe prediction raises the `--setting` message, and `resume --setting` then finishes with no new paid call;
  - archive moves `draft-sam/`;
  - a crash during the lay-back, then a rerun, makes zero new Seedance or SAM creates.
- **CLI:**
  - every refusal happens before any question;
  - the summary comes before the rights question;
  - the draft price is 13 s plus `sam3`;
  - the lay-back-only question, both with a saved Seedance record and with `draft-raw.mp4`; a remake's price includes `sam3`;
  - the SAM spend is logged exactly once (`{stage}-paste`), and `NoMasks` on the result gives B = ∅;
  - `repair` is refused on a scene job.
- **Existing jobs:** the whole current suite passes unchanged. One test checks that a single-shot job never reads `full-scene.mp4` and never calls the lay-back.
- **Web:** for a scene job, the draft wipe's "before" side is `full-scene.mp4`. Single-shot pages stay as they are.
- **Live (with Frank's OK, about $2.92):** one `@pytest.mark.live` draft on the test clip. Then check by eye in the app: the seam, the shoulder, the sharpness, and the 2.4:1 result size.

## Out of scope

- More than one part or more than one person per scene (YAGNI).
- `--shot N`, a cut list command, manual crop flags.
- Correcting a camera move or reframe made by the AI (global alignment).
- PiAPI, Wan and `face_mesh` mode for scene swaps.
- Stream-copying untouched shots for bit-exact output.
- Any app buttons.
