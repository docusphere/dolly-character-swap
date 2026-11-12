# WaveSpeed Seedance generator: design

Status: approved in conversation 2026-11-12; awaiting written review.
Builds on: `2026-10-22-character-swap-v1-design.md` and Plan 2b (Seedance on PiAPI, branch `feat/plan-2b-seedance`).
Branch: `feat/wavespeed`, created from `feat/plan-2b-seedance`.

## Why

The public YouTube tool needs a Seedance provider that viewers can sign up for and use without friction.

WaveSpeed runs the same Seedance 2.5 model:
- pay per use, with $1 of free credit and no subscription;
- it accepts a real face as a plain photo, with no face library;
- it worked in three live runs on 2026-11-12 (480p, 4 s clip, about 3 minutes and about $0.88 each).

PiAPI needs a $15/month plan, and its real faces must first become library assets that expire after a few idle days.

## Decisions

| # | Decision | Why |
|---|---|---|
| W1 | WaveSpeed is the default Seedance provider. PiAPI stays as an option. | Easiest for viewers, and no finished work is thrown away. |
| W2 | The provider is chosen at prepare time (`--provider wavespeed\|piapi`) and saved on the job. | The draft and final must use the same provider (a PiAPI promote needs a PiAPI draft), and faces differ at prepare time. |
| W3 | The generator stays `seedance`; `provider` is a separate job field. | Many "is this Seedance?" checks (prep, prompt, whole seconds) stay correct unchanged. |
| W4 | The default Seedance mode becomes `depth_mesh`. | Live test: much better lip sync than `depth`. Likeness was a little weaker, and the user prefers the mesh. |
| W5 | WaveSpeed finals are fresh runs: 720p by default, or `--resolution 1080p`. | WaveSpeed has no draft upgrade and no seed parameter. |
| W6 | Two-person clips stay PiAPI-only for now. | The PiAPI two-person path is still untested live; add WaveSpeed later (it accepts up to 30 reference images). |

## User-facing behaviour

### prepare
- `charswap prepare clip.mp4 --character me.jpg` defaults to `--provider wavespeed` and mode `depth_mesh`.
- **Real faces on WaveSpeed:** `--character IMAGE` works for real people. The tool shows its existing rights confirmation, and the PiAPI note ("real faces must use --face") is not shown.
- **`--face NAME`** (the PiAPI face library) is refused on WaveSpeed before any cost: "Saved faces are a PiAPI feature; use --character me.jpg with WaveSpeed, or --provider piapi."
- **`--face2` / `--character2`** are refused on WaveSpeed before any cost, with a message that two people need `--provider piapi` for now.
- **Wan:** `--provider` is refused with `--generator wan`, because Wan always runs on Replicate.

### prompt
- `prompt.txt` uses the provider's input names so the file is exactly what gets sent:
  - WaveSpeed: `@Video 1`, `@Image 1`;
  - PiAPI: `@video1`, `@image1`.
- The prompt check (that it mentions the video and image) uses the same per-provider names.

### generate
- `--draft` is 480p.
- `--final` is 720p by default; `--resolution 1080p` is also allowed.
  - PiAPI keeps its current behaviour (1080p promote by default, 720p fresh).
  - A WaveSpeed final with `--resolution` values outside 720p/1080p is refused before asking.
- Before the yes/no, a WaveSpeed final says: "The final is a new take at higher resolution; it will look a little different from the draft."
- The request sends `generate_audio: false`. The original soundtrack is restored afterwards (existing step).

### Old jobs
A Seedance job whose state has no `provider` field is treated as follows:
- if a draft or final provider record already exists (`draft-prediction.json` / `final-prediction.json`), it stays on **PiAPI**, so a paid task is never orphaned;
- otherwise it is treated as **WaveSpeed**.

The resolved value is written back to the state the first time it is read.

### doctor, viewer, README
- **`charswap doctor`** reports whether `WAVESPEED_API_KEY` is set. It reports `PIAPI_API_KEY` as optional ("only for --provider piapi").
- **The viewer** labels cost rows and the "Prompt sent" section with the job's provider (WaveSpeed or PiAPI).
- **README / SKILL:** "Sign up at WaveSpeed" comes first. PiAPI moves to an "Other providers" section.

## Architecture

```
providers/wavespeed.py               the ONLY code that calls WaveSpeed
providers/generators/
    seedance_wavespeed.py            Generator protocol (base.py) for WaveSpeed
    seedance_piapi.py                unchanged except where noted
```

The services registry is keyed by **generator plus provider**: `wan`, `seedance:piapi`, `seedance:wavespeed`.
- A single helper, `generator_key(job)`, resolves it, including the old-job rule.
- `KEYS` and `UNCONFIRMED` in the CLI use the same keys.
- Services builds the WaveSpeed generator only when `WAVESPEED_API_KEY` is set, mirroring PiAPI today.

### `providers/wavespeed.py` (WaveSpeedClient)
- **Auth:** `Authorization: Bearer <key>`. The key is never logged or printed.
- **`upload(path) -> url`:**
  1. `POST /api/v3/media/uploads` with `{filename, size}`;
  2. `PUT` the bytes to `data.upload.url` with `data.upload.headers` (no auth header);
  3. return `data.download_url`.
  - Files are kept 7 days. The limit is 200 MB, checked before upload, and the existing `fit_for_upload` compression is reused.
- **`run(body, record, on_created) -> output_url`:**
  - **Resume:** if `record` exists, it holds `{"id": ...}`. Poll that task and never POST again.
  - **Create:**
    1. write the exclusive "creating" claim marker;
    2. `POST /api/v3/bytedance/seedance-2.5/video-edit` once;
    3. save `data.id` to `record` before waiting;
    4. call `on_created`.
  - **Poll:** `GET /api/v3/predictions/{id}/result` until `data.status == "completed"`, then return `data.outputs[0]` (a string, or an object with `url`).
    - Terminal failures: `failed`, `cancelled`, `timeout`, `deleted`. These raise with WaveSpeed's `error` text and say the task was created (it may be charged; check the dashboard).
    - Polling backs off from 5 s to 15 s. A GET returning 429 or 5xx is retried; only the POST is never retried.
- **Error mapping on the create POST** (same rules as the other providers):
  - 400 / 401 / 403 / 404 / 422 → `WaveSpeedRejected(status_code)`, "WaveSpeed rejected the request; nothing was charged". The claim marker is released.
  - 429 → not retried; "WaveSpeed is busy; nothing was charged, try again in a minute". The marker is released.
  - 5xx, a timeout, an unreadable body, or a 200 with no `data.id` → ambiguous. The marker is kept, with the message "check your WaveSpeed dashboard (Predictions) before running again".
- **`download(url, dest)`:** an immediate download, done with the same helper as PiAPI.

### `providers/generators/seedance_wavespeed.py`
- `name = "seedance"`, `provider = "wavespeed"`, `model = "bytedance/seedance-2.5/video-edit"`.
- `supports_real_faces = True` (plain photo); `supports_secondary = False`.
- **`resolution(request, stage)`:** the draft is `480p`. A final uses `request.final_resolution` or `720p`, and must be one of `720p` / `1080p`.
- **`build_input(request, video_url, image_urls, resolution)`** returns:
  ```
  {prompt, video: video_url, reference_images: image_urls, resolution, generate_audio: false}
  ```
  The price is built from the same request.
- **`estimate`:** `cost_line(f"wavespeed-{res}", s)` plus `cost_line(f"wavespeed-{res}-input", s)`, with `s = ceil(seconds)` clamped to 4–30.
- **`generate`:**
  - free checks first: the prompt is not empty and has the WaveSpeed input names;
  - then upload the video (the compressed copy is deleted afterwards, as for PiAPI) and the character photo, then `run`, then `download`;
  - an existing record resumes without uploading.

### Costs (`costs.py`)
New price keys, per second, dated 2026-11-12, source `wavespeed.ai/models/bytedance/seedance-2.5/video-edit`:

| Key | $/s |
|---|---|
| `wavespeed-480`, `wavespeed-480-input` | 0.11 |
| `wavespeed-720`, `wavespeed-720-input` | 0.22 |
| `wavespeed-1080`, `wavespeed-1080-input` | 0.55 |

Example: a 4 s draft costs (4 + 4) × 0.11 = $0.88.

## Money-safety rules (unchanged, now also for WaveSpeed)

1. One POST per paid create. No automatic retry, including on 429.
2. The claim marker comes before the POST, and the task id is saved before waiting. Resume never re-buys.
3. An ambiguous result keeps the marker and points to the dashboard.
4. The price is shown from the same request object that is sent.
5. Every free refusal (`--face` on WaveSpeed, a second person, a bad resolution, an empty or mismatched prompt, an upload that is too large) happens before the cost question and before any archive.
6. A failed attempt never changes defaults (for example, the saved final resolution).
7. A paid job that is still running is never moved aside. The existing remake guards are reused unchanged.
8. Resume never depends on user-editable files.

## Testing

- **Unit tests** with a `FakeWaveSpeed` in `tests/fakes.py`. They cover:
  - upload (ticket, PUT, size limit);
  - create and save id, then resume without POST;
  - each error class (rejected, busy, ambiguous);
  - terminal failure statuses;
  - output as a string or an object;
  - the old-job provider rule;
  - CLI refusals and the "new take" note;
  - prompt token style;
  - estimates.
- The coverage gate stays at 85% or above, and ruff must be clean.
- **One live test** (`@pytest.mark.live`) runs a 4 s 480p draft (about $0.88) through `charswap generate --draft`. It only runs with the user's explicit OK.

## Out of scope

- Two-person clips on WaveSpeed (W6).
- Removing PiAPI.
- BytePlus.
- WaveSpeed webhooks or sync mode.
- Changing the job viewer's layout.
