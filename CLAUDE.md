# Character Swap

Local app + CLI that swaps a person in a short video for a chosen character, keeping motion, timing and the original soundtrack. Generators: Seedance 2.5 on WaveSpeed (default; real faces as a plain photo) or on PiAPI (saved faces, two people, 1080p upgrade), and Wan 2.2 Animate on Replicate (open source).

## Stack
Python 3.12 · uv · PyAV/NumPy/Pillow · httpx · MediaPipe · FastAPI/Jinja2 (read-only walkthrough app: `charswap app`, alias `view`) · pytest (+cov ≥ 85 %) · ruff.

## Commands
- Install: `uv sync`
- Tests (with coverage gate): `uv run pytest`
- Lint/format: `uv run ruff format src tests && uv run ruff check --fix src tests`
- CLI: `uv run charswap --help`
- Faces (free): `uv run charswap face add|list|check|renew`
- Prompt (Seedance): `uv run charswap prompt <job>`
- One shot of a scene: `uv run charswap prepare scene.mp4 --from S --to S --subject "..." --character me.jpg` (`--setting TEXT` skips the paid description; also on `resume`)
- Generate (paid, asks first): `uv run charswap generate <job> --draft|--final [--resolution 720p|1080p]`
- Scene swap edge (free, local): `uv run charswap edge <job> tight|normal|wide [--final]` (re-lays from saved files; saved as `edge` in state.json; also writes `{stage}-area.mp4`)
- Walkthrough app: `uv run charswap app` (read-only)

## Layout
- `src/character_swap/providers/` is the ONLY code that calls the network.
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
- Scene swaps (`segment` in `state.json`; `pipeline.is_scene_job`): `media/scene.py` finds cuts and bars, cuts the part, pastes back; `stages/paste.py` lays the result back inside generate. Single-shot jobs never take these branches.
- `web/` = read-only walkthrough app (FastAPI + Jinja2, localhost only; GET routes only, never changes job state). Texts live in `web/steps.py`, screens in `web/pages.py`.
- A job's Seedance provider (`provider` in `state.json`) is fixed at prepare; old jobs are resolved by `pipeline.generator_key`.
- Jobs live in `runs/<id>/` with `state.json` (resumable). Saved faces live in `faces/` (`faces.json` + photos).
- `inputs/`, `faces/` and `runs/` are git-ignored and never committed.

## Hard rules
- Never auto-retry a paid request. Only exception: a Replicate create rejected with 429 (nothing was created) is retried after re-claiming the exclusive "creating" marker. PiAPI and WaveSpeed 429s are not retried. Persist provider IDs before waiting.
- Every paid action shows an estimate and needs confirmation.
- Tests never call paid APIs (use `tests/fakes.py`); live tests are `@pytest.mark.live`.
- Stage files by name; never `git add -A`. No attribution lines in commits.
- Never copy anything from `_legacy/` assets or mention the original author's brands.

## Docs (read on demand)
Spec: `docs/superpowers/specs/` · Plans: `docs/superpowers/plans/` · Journal: `activity.md` · Checklist: `docs/scratchpad.md` · UI guide: `docs/ui-ux-best-practices.md`
