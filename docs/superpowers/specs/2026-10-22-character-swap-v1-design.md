# Character Swap v1 — Design Spec

Date: 2026-10-22
Status: Draft, awaiting user review
Working name: "Character Swap" (placeholder; one find-and-replace to rename)

## 1. Problem

Frank wants his own open-source, locally-run workflow that swaps a person in a short video for a character (usually himself) while keeping the original motion, timing and soundtrack. It will be demoed on YouTube and shipped as a downloadable repo + Claude Code skill.

The starting point is a friend's MIT project (copied into `character-swap/`, history removed, rebranded). It works, but:

- Its only video generator is a third-party reseller (removed from this project).
- It uploads private media to public file hosts (tmpfiles / catbox, the latter permanent).
- It needs a Cloudflare Quick Tunnel just to satisfy that reseller's webhook requirement.
- Face-mesh modes need a second Python environment.
- Code is 15 flat files of very dense one-liners: hard to read, explain on camera, or extend.

## 2. Goals / non-goals

Goals
- Same core technique: keep the motion, hide the identity (depth + SAM composite, optional face mesh, +3 semitone isolated vocals), generate, restore the original soundtrack.
- Real-face support at Seedance quality (1080p, prompt control) via a provider viewers can sign up for themselves.
- A fully open-source alternative path.
- Readable, tested, well-structured Python a non-developer can follow on camera.
- Simple setup: install `uv` + Rubber Band (`brew install rubberband` / `apt install rubberband-cli`), `uv sync`, fill `.env`, run. Rubber Band is a system binary, so `charswap doctor` checks for it.
- Never spend money without showing an estimate and getting confirmation.

Non-goals (v1)
- Hosted / multi-user deployment, auth, queues.
- BytePlus direct integration (slot reserved; phase 2 if enterprise access is granted).
- Hosted public demo website (e.g. inside Frank's "Dolly" creator site, bring-your-own-keys). Separate sub-project with its own spec after v1 works locally: it needs a server that can run video processing, per-user key handling and abuse protection.
- Local GPU inference.
- Multi-person face mesh (MediaPipe single centered speaker, as before).

## 3. Decisions (from brainstorming)

| # | Decision | Why |
|---|---|---|
| D1 | Rebuild as a proper Python package (approach A), reusing the proven processing logic | Readability, testability, explainable on camera |
| D2 | Main generator: **PiAPI Seedance 2.5 less-restriction** | Real faces via verified asset library, 2.5, draft→1080p promote, 4–30 s |
| D3 | Open-source generator: **Wan 2.2 Animate Replace on Replicate** | No face filter, Apache-2.0 model, one key with prep steps |
| D4 | Dropped: Seedance on Replicate | Blocks real faces; PiAPI covers its features |
| D5 | All three prep modes kept (depth, face_mesh, depth_mesh), Seedance only | They exist to pass Seedance's filters; Wan takes the original video |
| D6 | Wan uses a "direct" mode (no prep) | Wan does its own pose/face extraction and has no prompt input |
| D7 | Single Python environment (MediaPipe installs on 3.12) | Removes `.venv-face` and `setup-face-mesh.sh` |
| D8 | `uv` for install/run, Python pinned to 3.12 | Viewers install one tool; uv fetches Python itself |
| D9 | File hosting: Cloudflare R2 presigned links (default) + tmpfiles fallback | PiAPI needs fetchable URLs; R2 is private + expiring; tmpfiles = zero setup |
| D10 | Remove cloudflared tunnel + webhook receiver | Polling only |
| D11 | Improvements: Whisper transcript in prompt, auto scene description, cost preview + spend log, face check | Chosen by user |
| D12 | Never auto-retry paid requests; persist IDs before waiting | Kept from original |

## 4. Architecture

```
character-swap/
├── CLAUDE.md                 short project rules + commands
├── README.md                 viewer-facing
├── activity.md               decision journal
├── pyproject.toml            deps, ruff/pytest config, `charswap` entry point
├── .env.example
├── .claude/skills/character-swap/SKILL.md
├── docs/
│   ├── scratchpad.md  SETUP.md  MODELS.md  TROUBLESHOOTING.md
│   └── superpowers/specs/ plans/
├── src/character_swap/
│   ├── config.py             typed settings from .env; fails fast on missing keys
│   ├── jobs.py               job folder layout, state.json, resume
│   ├── review.py             approval gate bound to artifact hashes
│   ├── costs.py              price table, estimates, spend log
│   ├── prompts.py            builds the generation prompt (template + transcript + scene + mode notes)
│   ├── media/
│   │   ├── video.py          decode/encode, normalize (24 fps, ≤960 px, length cap per generator)
│   │   ├── composite.py      depth-subject-over-background compositing
│   │   └── audio.py          extract, embed vocals, restore original soundtrack
│   ├── stages/               one pure-ish function per pipeline step
│   │   ├── separate.py       Demucs vocals (Replicate)
│   │   ├── pitch.py          Rubber Band +3 semitones
│   │   ├── transcribe.py     Whisper (Replicate)
│   │   ├── describe.py       scene description, people excluded (Replicate VLM)
│   │   ├── depth.py          Depth Anything Video (Replicate) [+ HF alt]
│   │   ├── masks.py          SAM 3 raw masks (Replicate)
│   │   └── face_mesh.py      MediaPipe Face Landmarker (local)
│   ├── providers/
│   │   ├── replicate.py      the only Replicate client: upload, run, poll, persist IDs
│   │   ├── piapi.py          the only PiAPI client: task create/get, assets
│   │   ├── hosting/          base.py · r2.py · tmpfiles.py  (public fetchable URLs)
│   │   └── generators/
│   │       ├── base.py       Generator protocol + GenerationRequest/Result
│   │       ├── seedance_piapi.py
│   │       └── wan_replicate.py
│   ├── pipeline.py           orchestrates stages per mode; resumable
│   ├── cli.py                `charswap` commands
│   └── web/
│       ├── app.py            FastAPI app factory + routes
│       └── static/index.html
└── tests/                    mirrors src/; fakes for every provider
```

Rules
- Only `providers/*` talk to the network. Stages receive a provider object (dependency injection), so tests pass fakes.
- Each module has one job and a docstring stating it. Target < 200 lines per file.
- No absolute paths; everything resolves from the project root or job folder.

## 5. Data flow

```
upload ─► save original (never modified; used for final audio)
       ─► normalize (24 fps, ≤960 px; length ≤ generator max)
       ─► [Seedance only]
            separate vocals (Demucs) ─► pitch +3 (Rubber Band)
            transcribe (Whisper) ─► exact words
            describe scene (VLM, no people)
            mode=depth       depth ─► SAM masks ─► depth subject over real background
            mode=face_mesh   landmarks ─► mesh over original
            mode=depth_mesh  depth composite ─► mesh on top
            embed pitched vocals into prepared video
       ─► REVIEW GATE (user watches full clip, approves; locked to artifact hashes)
            reject ─► repair (depth modes: re-run SAM 3 on the original RGB frames,
                      keep depth colors, recomposite) ─► approval cleared ─► review again
       ─► PROMPT REVIEW: auto-built prompt shown in an editable box; the confirmed text is what is sent
       ─► cost estimate ─► user confirms
       ─► generate draft
            Seedance: 480p draft (task_type seedance-2.5-less-restriction)
            Wan:      original video + character image, 480
       ─► download ─► discard generated audio ─► restore original soundtrack
       ─► user approves final ─► cost estimate ─► confirm
            Seedance: promote draft with draft_task_id → 1080p
            Wan:      re-run at 720 with same seed
       ─► restore soundtrack again ─► final.mp4
```

Wan "direct" mode skips everything between normalize and the review gate, and skips PROMPT REVIEW (Wan has no prompt input; the `prompt` field is ignored). For Wan, the primary character image is collected at `POST /jobs` so the review gate can show and hash it.

Review gate contents per mode (all hashed; any change clears approval):
| Mode | What the user reviews | Hashed artifacts |
|---|---|---|
| depth | full composite, mask coverage, pitched vocals | depth video, masks, composite, embedded-audio video |
| face_mesh | mesh tracking (eyes, mouth, alignment), pitched vocals | landmarks json, mesh video, embedded-audio video |
| depth_mesh | both of the above | union of both sets |
| wan direct | normalized source + character image | normalized video, character image |

Prep details kept from the original: SAM subject text prompt (default "person", editable); depth provider = Replicate Depth Anything Video by default, Hugging Face Space as the one alternative (the second Replicate depth model is dropped).

Length limits: clips shorter than the generator's `min_seconds` (Seedance 4 s) or longer than `max_seconds` are rejected at upload with a clear message.

Job folder (`runs/<job_id>/`) holds every artifact plus `state.json` (stage statuses, provider task IDs, hashes, costs). Any command can resume from it.

## 6. Providers

### 6.1 Generator interface

```python
@dataclass(frozen=True)
class CharacterRef:
    image_path: Path            # local copy, always present (Wan needs a file)
    face_asset_id: str | None   # PiAPI asset id when the image is a verified face

@dataclass(frozen=True)
class GenerationRequest:
    prepared_video: Path        # Seedance: prepared composite; Wan: normalized original
    primary: CharacterRef
    secondary: CharacterRef | None   # Seedance only
    prompt: str                 # final text the user saw and confirmed
    duration_s: int
    seed: int                   # chosen once per job, reused for the final

class Generator(Protocol):
    name: str
    min_seconds: int
    max_seconds: int
    supports_real_faces: bool
    supports_secondary: bool
    def estimate(self, req: GenerationRequest, stage: Literal["draft", "final"]) -> Cost: ...
    def submit_draft(self, req: GenerationRequest) -> str: ...                       # task id, persisted first
    def submit_final(self, req: GenerationRequest, draft: GenerationResult) -> str: ...
    def poll(self, task_id: str) -> GenerationResult: ...                            # pending | done(url) | failed(reason)
```

The full `GenerationRequest` is saved in `state.json`, so any final path (promote or re-run) has everything it needs.

Draft / final per generator:
| Generator | Draft | Final |
|---|---|---|
| PiAPI Seedance 2.5 | 480p | promote `draft.task_id` → 1080p |
| PiAPI Seedance 2.0 fallback | 480p | re-run at 1080p, same request + seed |
| Wan | always 480 | re-run at 720, same request + seed |

### 6.1a Character references
- Primary (required) is either a saved face from the face library (`faces.json` → local image + `asset://` id) or an uploaded image (stylized / fictional character; hosted URL, no asset).
- Secondary (optional, Seedance only) is an uploaded image. Mapping: primary = `@image1`, secondary = `@image2` in `image_urls` order and in the prompt.
- Wan receives only `primary.image_path`; the UI hides the secondary slot for Wan.
- Video identity references (the original's Omni/multi_reference path) are **dropped in v1** (YAGNI); images only.

### 6.2 PiAPI Seedance 2.5 (`seedance_piapi.py`)
- Endpoint `POST https://api.piapi.ai/api/v1/task`, header `X-API-Key`.
- `model: "seedance"`, `task_type: "seedance-2.5-less-restriction"`, `mode: "omni_reference"`.
- Inputs: `video_urls=[prepared]`, `image_urls=[asset://<face_id> or hosted image]`, `prompt`, `duration` (4–30), `resolution: "480p"` for draft, `audio: false`.
- Final: promote with `draft_task_id` → 1080p.
- Polling: get-task endpoint (exact path to confirm, see §11).
- Fallback if 2.5 rejects `asset://`: `seedance-2-less-restriction` (15 s max, no promote; final = 1080p re-run with same seed). The fallback is a manual choice offered to the user, and it re-checks clip length (15 s cap) before submitting.

### 6.3 Face assets (`providers/piapi.py`)
- `POST /api/v1/asset/upload` with a hosted URL valid ≥ 24 h (hence R2).
- Poll `GET /api/v1/asset/{id}` until `Active` / `Failed`; store in `faces.json` (name, asset id, status, created).
- Requires PiAPI Hobbyist plan or higher. Assets purge after idle TTL (3–15 days by plan); the app re-checks status before use and offers re-upload.
- UI and CLI state that only faces the user has rights to may be uploaded.

### 6.4 Wan 2.2 Animate (`wan_replicate.py`)
- `wan-video/wan-2.2-animate-replace`, inputs `video`, `character_image`, `resolution` (480/720), `seed`, `merge_audio=false` (we restore audio ourselves).
- Billed by GPU time; estimate from a configurable per-second-of-video figure refined after first runs.

### 6.5 File hosting
- `HostingProvider.put(path, ttl_hours) -> url`.
- R2: S3-compatible upload to a private bucket, presigned GET URL with TTL (default 24 h). Env: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
- tmpfiles: used when R2 is not configured; UI warns "public link, ~60 min". Face upload refuses tmpfiles (TTL too short) with a clear message.
- Every URL is checked to return media bytes (not HTML) before use.

### 6.6 Replicate (`providers/replicate.py`)
- Private file upload via `POST /v1/files`, predictions with pinned versions where available, polling, IDs persisted before waiting.
- Models: Demucs (pinned), Depth Anything Video, SAM 3 video (pinned), Whisper, a vision-language model for scene description (chosen in planning after schema check), Wan Animate.

## 7. Costs (`costs.py`)
- `prices.toml` (or a dict in code) holds per-unit prices with a `checked_on` date per entry.
- Every paid action computes an estimate and requires confirmation (UI dialog / CLI `--yes`).
- After completion, append to `runs/<job>/costs.json` and `runs/spend-log.csv`: timestamp, job, provider, model, task id, estimate, units.
- UI shows job total and all-time total. Labelled "estimate — check provider billing".

## 8. Face check
`charswap check-face <photo>` / UI button:
1. Upload to host → create PiAPI asset → wait for `Active`/`Failed`.
2. If Active, optionally run the cheapest real request (4 s, 480p, tiny prepared clip) after cost confirmation, to prove the generator accepts it.
3. Report: accepted / rejected (+ provider message). Saved to `faces.json`.

## 9. Web UI & API
- Steps: Upload → Review → Draft → Approve & export. Mode picker: "Seedance 2.5 · {Depth | Face mesh | Depth + mesh}" or "Wan · Direct (open source, 720p)".
- Face panel: upload once, status badge, reuse.
- Cost estimate shown on every paid button; spend total in header.
- Routes: `POST /jobs` (video, generator, mode, subject), `GET /jobs/{id}`, `GET|POST /jobs/{id}/review` (approve / reject), `POST /jobs/{id}/repair`, `GET /jobs/{id}/prompt` (auto-built draft prompt), `POST /jobs/{id}/draft`, `POST /jobs/{id}/final`, `GET /jobs/{id}/estimate?step=draft|final`, `GET|POST /faces`, `GET /health`.
- `POST /jobs` with `generator=wan` also requires `primary_face_id` or `primary_image` (see §5).
- `POST /jobs/{id}/draft` fields: `prompt` (confirmed text), `primary_face_id` **or** `primary_image` (file), optional `secondary_image` (Seedance only), `confirm_cost=true`.
- Bind to 127.0.0.1 only. Follow `docs/ui-ux-best-practices.md` (copied into this repo from Frank's guide) during build; dark/light tokens in one place.
- `charswap doctor` and `GET /health` report: Python, `rubberband` binary, ffmpeg libs (PyAV), each key present, hosting mode (R2 / tmpfiles).

## 10. Error handling
- Missing key / config → fail at startup or command start with exactly which variable and where to get it.
- Paid POST never auto-retried. Task IDs written to `state.json` before polling. Timeouts leave the job resumable (`charswap resume <job>`).
- Clip longer than generator max → rejected before any upload.
- Face rejected / asset failed / URL expired / provider moderation error → specific human-readable message + saved raw response.
- Free calls (uploads, status checks) retry with backoff (max 3).

## 11. Open items to verify during planning (before code)
1. PiAPI get-task endpoint path and status casing.
2. Whether `seedance-2.5-less-restriction` accepts `asset://` IDs (docs conflict) and exact draft/promote request bodies.
3. PiAPI Hobbyist plan price and asset quota.
4. Wan Animate: max input length and typical run time/cost.
5. Replicate vision model for scene description (schema + price).
6. Whether the depth/identity-hiding prep is still needed with less-restriction (keep regardless; test in demo).

## 12. Testing
- pytest, one test module per source module; fakes for `ReplicateClient`, `PiAPIClient`, `HostingProvider`, `Generator`.
- Unit: normalize length caps, compositing (port existing test), audio restore byte-equality, review hash invalidation, cost math, prompt building, state/resume transitions, config validation.
- Integration (no network): full pipeline per mode with fakes, including resume after simulated timeout.
- Web: FastAPI TestClient for each route, including "draft refused without approval" and "paid route refused without confirmation".
- Live (opt-in, marked `@pytest.mark.live`, never in default run): face check, one 4 s 480p draft.
- Coverage: `pytest-cov` on `src/character_swap`, **minimum 85% line + branch coverage, enforced** (`--cov-fail-under=85`); providers' real network code is exercised through recorded/fake HTTP responses (e.g. `respx`/`responses`-style fakes), not live calls. Coverage report printed on every run; HTML report optional.
- Checks before "done": `uv run ruff format --check`, `uv run ruff check`, `uv run pytest` (includes coverage gate).
- Manual browser checklist provided after UI work.

## 13. Deliverables beyond code
- README (viewer-facing: what it does, legal/consent note, setup in ≤ 5 steps, costs).
- `.claude/skills/character-swap/SKILL.md` (setup + run workflow for Claude Code).
- `CLAUDE.md`, `activity.md`, `docs/scratchpad.md`, `docs/SETUP.md`, `docs/MODELS.md`, `docs/TROUBLESHOOTING.md`.
- Remove: `webhook.py`, `launch.py` tunnel logic, `media_host.py` catbox, `setup-face-mesh.sh`, `mesh_worker.py` subprocess split, the old reseller API doc in `docs/`, `.agents/`, `AGENTS.md` (replaced by `CLAUDE.md` + SKILL.md), `docs/SETUP-AGENT.md`, `docs/AGENT-OPERATIONS.md`, `docs/MEDIA-HOSTING.md`, `docs/VALIDATION.md` (content folded into the new SETUP / TROUBLESHOOTING / SKILL docs where still true), old flat modules after port.
- No commits without Frank's explicit OK.
