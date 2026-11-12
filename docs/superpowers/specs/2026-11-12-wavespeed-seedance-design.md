# WaveSpeed Seedance generator: design

Status: approved in conversation 2026-11-12; spec review passed after two revisions.
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
| W3 | The generator stays `seedance`; `provider` is a separate job field. Code that is really about PiAPI (promote, face library, wording) is gated on the provider, through generator attributes, not through `generator == "seedance"`. | Prep, prompt building and whole-second rules stay shared. Only PiAPI-specific behaviour moves behind a flag. |
| W4 | The default Seedance mode becomes `depth_mesh` for both providers. | Live test: much better lip sync than `depth`. The user prefers it. |
| W5 | WaveSpeed finals are fresh runs: 720p by default, or `--resolution 1080p`. | WaveSpeed has no draft upgrade and no seed parameter. |
| W6 | Two-person clips stay PiAPI-only for now. | The PiAPI two-person path is still untested live. |

## User-facing behaviour

### prepare
- `charswap prepare clip.mp4 --character me.jpg` defaults to `--provider wavespeed` and mode `depth_mesh`.
- **Real faces on WaveSpeed:** `--character IMAGE` works for real people.
  - The prepare command asks a yes/no rights confirmation before the cost question. It uses a provider-neutral consent sentence ("You confirm you have the right to use this person's face…"). `faces.CONSENT` keeps its PiAPI face-library wording for `face add`.
  - `--yes` answers yes to both questions; the consent text is still printed.
  - The PiAPI note ("real faces must use --face") is shown only for `--provider piapi`.
- **`--face NAME`** (the PiAPI face library) is refused on WaveSpeed before any question: "Saved faces are a PiAPI feature; use --character me.jpg with WaveSpeed, or --provider piapi."
- **`--face2` / `--character2`** are refused on WaveSpeed before any question: "Two people need --provider piapi for now."
- **Wan:** `--provider` is refused with `--generator wan`.
- **Wording updates:**
  - the `--character` help;
  - the "Seedance needs --face NAME…" error, which becomes provider-aware;
  - the `--provider` help;
  - the EPILOG examples (WaveSpeed first).

### prompt
- `prompts.build_prompt` / `default_prompt` take the provider, so `prompt.txt` is exactly what gets sent. The input names are:
  - WaveSpeed: `@Video 1`, `@Image 1`;
  - PiAPI: `@video1`, `@image1`, `@image2`.
  - `BASE`, `MODE_NOTES` and `SECONDARY` become templates filled with the provider's names.
- The placeholder check (`_check_placeholders` in `pipeline.py`) takes the provider and checks its names.
- **An old `prompt.txt` written with PiAPI names, on a job that resolves to WaveSpeed:** when `charswap prompt` or `charswap generate` runs, it rewrites `@video1` → `@Video 1` and `@image1` → `@Image 1`, and prints "Updated prompt.txt to WaveSpeed's input names." This is free and happens before any question.

### generate
- `--draft` is 480p.
- **Final resolutions come from the generator,** not from a table keyed by generator name:
  - each generator exposes `finals` (the first one is the default) and `promotes` (bool);
  - `pipeline.FINAL_RESOLUTIONS` and `final_resolution()` use `generator_key(job)`;
  - WaveSpeed: `finals = ("720p", "1080p")`, `promotes = False`;
  - PiAPI: `finals = ("1080p", "720p")`, `promotes = True`, unchanged.
- **Places that today assume "Seedance 1080p final = PiAPI promote" must check `generator.promotes`:**
  - `build_request` (sets `promote` and so `face_needed`);
  - `generation_prompt` for a final: every fresh-run final (any WaveSpeed final; a PiAPI 720p final) reuses the paid draft's saved prompt and is refused with the "draft's prompt wasn't saved" message if it's missing;
  - the CLI `promote_body` pre-check;
  - the CLI "1080p (the default) upgrades the exact draft" line;
  - `_after_draft_hint`.
- Before the yes/no, a WaveSpeed final says: "The final is a new take at higher resolution; it will look a little different from the draft."
- A WaveSpeed `--resolution` outside 720p/1080p is refused before asking.
- The request sends `generate_audio: false`. The original soundtrack is restored afterwards (existing step).

### Old jobs (no `provider` field)
`generator_key(job)` is a **pure function of the job folder** (state plus the files it checks). It takes the `Job`, not a bare state dict. A Seedance job with no `provider` is treated as **PiAPI** when any of these is true:
- a `face_primary` / `face_secondary` or `character_secondary` entry;
- a draft or final record, or a raw file (`draft-prediction.json`, `final-prediction.json`, `draft-raw.mp4`, `final-raw.mp4`);
- a done draft or final stage.

Otherwise the job is treated as **WaveSpeed**.

Only `charswap prompt` and `charswap generate` write the resolved `provider` into the state. They write it right after loading the job: **before any paid create, never after**. Otherwise an interrupted WaveSpeed draft would leave a record that the rule then reads as PiAPI. The viewer only reads the provider and never writes it.

### doctor, settings, viewer, README
- **`config.WHERE_TO_GET`** gets `WAVESPEED_API_KEY: "https://wavespeed.ai (Dashboard > API Keys)"`.
- **`charswap doctor`:**
  - reports whether `WAVESPEED_API_KEY` is set, and `PIAPI_API_KEY` as optional ("only for --provider piapi");
  - its hosting line names both providers' temporary uploads.
- **Viewer** (read-only; uses `generator_key`):
  - cost rows and "Prompt sent" carry the provider's name;
  - the draft and final `seedance_why` texts become provider-aware (no "PiAPI" or "upgrades the exact draft" on WaveSpeed jobs);
  - `_generation_name`'s default resolution comes from the generator's `finals`.
- **README:** "Sign up at WaveSpeed" comes first; PiAPI moves to an "Other providers" section. There is no project SKILL file yet; it comes in Plan 3.

## Architecture

```
providers/wavespeed.py               the ONLY code that calls WaveSpeed
providers/common.py                  + shared download_now(url, dest, provider, keeps) helper
providers/generators/
    base.py                          + provider, finals, promotes on the Generator protocol
    seedance_wavespeed.py            Generator for WaveSpeed
    seedance_piapi.py                provider="piapi", finals=FINALS, promotes=True
```

- **Registry:** the services registry is keyed by `generator_key(job)`: `wan`, `seedance:piapi`, `seedance:wavespeed`.
  - `KEYS` and `UNCONFIRMED` in the CLI use the same keys.
  - Services builds each Seedance generator only when its key is set, as PiAPI is today.
  - A missing generator gives the existing "set X in .env" message, naming the right key.
- **Download helper:** the PiAPI download moves into the shared `download_now` helper. It takes the HTTP client and `sleep` as `PiAPIClient.download` does today, and still raises `OutputExpired` (PiAPI's message keeps "24 hours"). The expiry message names the provider and how long it keeps results (PiAPI: 24 hours; WaveSpeed: not documented, so "download right away; if it expired, delete the record and run again").

### `providers/wavespeed.py` (WaveSpeedClient)
- **Auth:** `Authorization: Bearer <key>`. The key is never logged or printed.
- **`upload(path) -> url`:**
  1. `POST /api/v3/media/uploads` with `{filename, size}`;
  2. `PUT` the bytes to `data.upload.url` with `data.upload.headers` (no auth header);
  3. return `data.download_url`.
  - Files are kept 7 days. The limit is 200 MB, checked before upload, and `fit_for_upload` is reused.
- **Record shape:** `{"id", "status", "task"}`, re-saved on every poll. WaveSpeed's statuses map to the pipeline's words, as `piapi.task_status` does:

  | WaveSpeed | Saved as |
  |---|---|
  | `created`, `queued`, `pending` | `starting` |
  | `processing` and any other non-terminal status | `processing` |
  | `completed` | `succeeded` |
  | `failed`, `cancelled`, `timeout`, `deleted` | `failed` |

  The exclusive claim marker uses status `creating`, as with the other providers.
- **`run(body, record, on_created) -> output_url`:**
  - **Resume (record exists):**
    - `creating` → raise the unconfirmed message (check the dashboard), with no POST;
    - `failed` → raise the failure message with no polling;
    - otherwise poll the saved id. A resume never POSTs.
  - **Create:**
    1. write the `creating` claim marker exclusively;
    2. `POST /api/v3/bytedance/seedance-2.5/video-edit` once;
    3. save `{id, status, task}` before waiting;
    4. call `on_created`.
  - **Poll:** `GET /api/v3/predictions/{id}/result` with a back-off from 5 s to 15 s. A GET returning 429 or 5xx is retried; the POST never is.
    - On `completed`, return `data.outputs[0]` (a string, or an object with `url`).
    - On a failure status, raise with WaveSpeed's `error` text and say the task was created (it may be charged; check the dashboard).
- **Error mapping on the create POST:**
  - 400 / 401 / 403 / 404 / 422 → `WaveSpeedRejected(status_code)`, "WaveSpeed rejected the request; nothing was charged". The marker is released.
  - 429 → not retried; "WaveSpeed is busy; nothing was charged, try again in a minute". The marker is released.
  - 5xx, a timeout, an unreadable body, or a 200 with no `data.id` → ambiguous. The marker is kept, with the message "check your WaveSpeed dashboard (Predictions) before running again".

### `providers/generators/seedance_wavespeed.py`
- `name = "seedance"`, `provider = "wavespeed"`, `model = "bytedance/seedance-2.5/video-edit"`.
- `supports_real_faces = True`, `supports_secondary = False`.
- `finals = ("720p", "1080p")`, `promotes = False`.
- `PRICE = {"480p": "wavespeed-480", "720p": "wavespeed-720", "1080p": "wavespeed-1080"}`.
- **`resolution(request, stage)`:** the draft is `480p`. A final uses `request.final_resolution` or `finals[0]`, and must be in `finals`.
- **`build_input(request, video_url, image_urls, resolution)`** returns:
  ```
  {prompt, video: video_url, reference_images: image_urls, resolution, generate_audio: false}
  ```
  The price shown is built from the same request.
- **`estimate`:** `cost_line(PRICE[res], s)` plus `cost_line(f"{PRICE[res]}-input", s)`, with `s = ceil(seconds)` clamped to 4–30.
- **`generate`:**
  - free checks first: the prompt is not empty and has WaveSpeed's names;
  - then upload the video (the compressed copy is deleted afterwards) and the character photo, then `run`, then `download_now`;
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
5. Every free refusal happens before the cost question and before any archive. That covers `--face` on WaveSpeed, a second person, a bad resolution, an empty or mismatched prompt, a missing saved draft prompt and an upload that is too large.
6. A failed attempt never changes defaults (for example, the saved final resolution).
7. A paid job that is still running is never moved aside. The existing remake guards are reused unchanged.
8. Resume never depends on user-editable files.

## Testing

- **Unit tests** with a `FakeWaveSpeed` in `tests/fakes.py`. They cover:
  - upload (ticket, PUT, size limit);
  - create and save the record, then resume without POST;
  - resume of a `creating` or `failed` record;
  - each error class;
  - the status mapping;
  - output as a string or an object.
- **Pipeline and CLI tests** cover:
  - the WaveSpeed default final is 720p;
  - a WaveSpeed 1080p final is a fresh run, not a promote;
  - a fresh final reuses the draft prompt;
  - the old-job provider rule (each PiAPI trigger);
  - the viewer never writes state;
  - prompt token style and old-token rewrite (which never triggers `draft_prompt_changed`);
  - the provider is written before the paid create, so an interrupted WaveSpeed draft resumes on WaveSpeed;
  - consent question plus `--yes`;
  - the refusals and the "new take" note;
  - estimates.
- The existing PiAPI tests must keep passing. The coverage gate stays at 85% or above, and ruff must be clean.
- **One live test** (`@pytest.mark.live`) runs a 4 s 480p draft (about $0.88) through `charswap generate --draft`. It only runs with the user's explicit OK.

## Out of scope

- Two-person clips on WaveSpeed (W6).
- Removing PiAPI.
- BytePlus.
- WaveSpeed webhooks or sync mode.
- A project SKILL file (Plan 3).
- Changing the job viewer's layout.
