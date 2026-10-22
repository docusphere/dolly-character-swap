# Provider API facts (verified 2026-10-22)

Source of truth for Plans 1–2. PiAPI publishes raw markdown docs: append `.md` to any docs URL, index at https://piapi.ai/docs/llms.txt.

## PiAPI (Seedance)

**Auth:** header `x-api-key`. **Create:** `POST https://api.piapi.ai/api/v1/task`. **Poll:** `GET https://api.piapi.ai/api/v1/task/{task_id}`.

Response: `{code, data:{task_id, status, output:{video|video_url}, meta:{usage}, error:{code,message,raw_message}}, message}`. `output` is null until done. Result URL valid 24 h → download immediately.

Status casing is inconsistent across docs → compare lowercase: `completed`/`success` = done, `failed` = error, anything else (pending, staged, starting, processing, retry) = running.

### Seedance 2.5 draft (480p)
```json
{"model":"seedance","task_type":"seedance-2.5-less-restriction",
 "input":{"prompt":"... @video1 ... @image1 ...","mode":"omni_reference",
  "omni_reference_task_type":"reference",
  "video_urls":["https://.../prepared.mp4"],"image_urls":["https://.../char.png or asset://id"],
  "duration":5,"aspect_ratio":"9:16","resolution":"480p","draft":true}}
```
- `draft:true` requires `resolution:"480p"`.
- `omni_reference_task_type`: `auto` (default; can fail post-submit if it guesses differently) | `reference` | `edit` (needs `aspect_ratio:"adaptive"`, `duration:-1`) | `extend`.
- `@video1/@image1/@audio1` placeholders must match supplied media or 400. Note lowercase in PiAPI docs.

### Promote draft → 1080p
```json
{"model":"seedance","task_type":"seedance-2.5-less-restriction",
 "input":{"draft_task_id":"<id>","resolution":"1080p"}}
```
Forbidden with promote (400 even if same value): prompt, image/video/audio urls, duration, aspect_ratio, seed, audio, omni_reference_task_type, draft. Draft must be completed, same account, ≤ 7 days old, same task_type.

### Limits & prices
- Duration 4–30 s (int). Resolutions 480p/720p/1080p.
- Raw refs: ≤ 30 images; ≤ 10 videos (mp4/mov, 2–30 s, ≤ 200 MB, total ≤ 30 s); ≤ 10 audio.
- Per second: `seedance-2.5` 0.15 / 0.35 / 0.80; `seedance-2.5-less-restriction` 0.165 / 0.385 / 0.88 (480p/720p/1080p). Input video billed at half rate. Promote also bills draft input video at half 1080p rate.

### `asset://` on 2.5-less-restriction — CONFLICTING
- Yes: blog "seedance-2-5-less-restriction" (2026-09-08) example uses `asset://` with 2.5-less-restriction; 2.5 doc mentions `auto_upload_assets`.
- No: older specs blog (2026-07-31); Private Asset Library doc lists only `seedance-2-less-restriction` and `seedance-2-fast-less-restriction`.
- → Verify with one cheap call (face check, Plan 2).

### Private Asset Library
- Plan ≥ "hobbyist" (pricing page lists Free / Creator $15 / Pro $60 / Enterprise $100 per month; hobbyist ≈ Creator, unconfirmed).
- `POST /api/v1/asset/upload` `{url, asset_type?, name?}` → 202 `{asset_id, status:"Processing", expires_at}`. URL must stay reachable ≥ 24 h.
- `GET /api/v1/asset/{id}` → `{status: Processing|Active|Failed, error?}` (also refreshes TTL). `GET /api/v1/asset/list`, `DELETE /api/v1/asset/{id}`.
- Image limits: < 30 MB, aspect 0.4–2.5, sides 300–6000 px. Asset video: ≤ 50 MB, 2–15 s.
- Idle TTL: hobbyist 3 days, developer 7, premium/enterprise 15.
- PiAPI temp upload `POST https://upload.theapi.app/api/ephemeral_resource` `{file_name, file_data: base64}` (Creator+, ≤ 10 MB, no mov, 24 h).

## Replicate

| Model | Call by | Inputs | Output | Cost |
|---|---|---|---|---|
| `cjwbw/demucs` | version `25a17310…d953` | audio, model_name, output_format | dict of stem URLs | ≈ $0.017/run |
| `lucataco/depth-anything-video` | latest (resolved) `91436914…5e6d` | video uri (schema-resolved) | URL | ≈ $0.040/run |
| `lucataco/sam3-video` | version `408f82fc…1269` | video, prompt, mask_only, return_zip | zip URL | ≈ $0.002/run |
| `openai/whisper` (community) | version `80996966…d16e` | audio, transcription | `{transcription, segments,…}` | ≈ $0.00075/run |
| `openai/gpt-4o-mini` (official) | model endpoint | prompt, image_input[], max_completion_tokens | token array | $0.15/M in, $0.60/M out |
| `wan-video/wan-2.2-animate-replace` (official) | model endpoint | video, character_image, resolution "480"/"720", seed, merge_audio, go_fast | mp4 URL | $0.02/s (480p), $0.05/s (720p) of output |

Wan max input length unpublished ("long videos process in segments").

## Cloudflare R2
boto3 `s3` client, `endpoint_url=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, `region_name="auto"`; `generate_presigned_url("get_object", …, ExpiresIn≤604800)`. Presigned URLs only on the S3 domain (not custom domains). Free tier: 10 GB-month, 1M Class A, 10M Class B, free egress.

## tmpfiles.org
`POST https://tmpfiles.org/api/v1/upload` multipart `file` (≤ 100 MB), optional `expire` seconds 60–172800 (default 3600). Response `data.url = https://tmpfiles.org/{id}/{name}`; direct link by convention `https://tmpfiles.org/dl/{id}/{name}` (unverified). Max 48 h. ToS: no commercial automation without permission. Face asset upload needs `expire ≥ 86400`.
