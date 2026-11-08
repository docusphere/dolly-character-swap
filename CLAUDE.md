# Character Swap

Local app + CLI that swaps a person in a short video for a chosen character, keeping motion, timing and the original soundtrack. Generators: PiAPI Seedance 2.5 (real faces, 1080p) and Wan 2.2 Animate on Replicate (open source).

## Stack
Python 3.12 · uv · PyAV/NumPy/Pillow · httpx · MediaPipe · FastAPI/Jinja2 (read-only job viewer: `charswap view`) · pytest (+cov ≥ 85 %) · ruff.

## Commands
- Install: `uv sync`
- Tests (with coverage gate): `uv run pytest`
- Lint/format: `uv run ruff format src tests && uv run ruff check --fix src tests`
- CLI: `uv run charswap --help`
- Faces (free): `uv run charswap face add|list|check|renew`
- Prompt (Seedance): `uv run charswap prompt <job>`
- Generate (paid, asks first): `uv run charswap generate <job> --draft|--final [--resolution 720p]`
- Viewer: `uv run charswap view`

## Layout
- `src/character_swap/providers/` is the ONLY code that calls the network.
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
- `web/` = read-only job viewer (FastAPI + Jinja2, localhost only; GET routes only, never changes job state).
- Jobs live in `runs/<id>/` with `state.json` (resumable). Saved faces live in `faces/` (`faces.json` + photos).
- `inputs/`, `faces/` and `runs/` are git-ignored and never committed.

## Hard rules
- Never auto-retry a paid request. Only exception: a Replicate create rejected with 429 (nothing was created) is retried after re-claiming the exclusive "creating" marker. PiAPI 429s are not retried. Persist provider IDs before waiting.
- Every paid action shows an estimate and needs confirmation.
- Tests never call paid APIs (use `tests/fakes.py`); live tests are `@pytest.mark.live`.
- Stage files by name; never `git add -A`. No attribution lines in commits.
- Never copy anything from `_legacy/` assets or mention the original author's brands.

## Docs (read on demand)
Spec: `docs/superpowers/specs/` · Plans: `docs/superpowers/plans/` · Journal: `activity.md` · Checklist: `docs/scratchpad.md` · UI guide: `docs/ui-ux-best-practices.md`
