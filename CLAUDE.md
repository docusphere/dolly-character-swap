# Character Swap

Local app + CLI that swaps a person in a short video for a chosen character, keeping motion, timing and the original soundtrack. Generators: PiAPI Seedance 2.5 (real faces, 1080p) and Wan 2.2 Animate on Replicate (open source).

## Stack
Python 3.12 · uv · PyAV/NumPy/Pillow · httpx · MediaPipe · pytest (+cov ≥ 85 %) · ruff. FastAPI UI arrives in Plan 3.

## Commands
- Install: `uv sync`
- Tests (with coverage gate): `uv run pytest`
- Lint/format: `uv run ruff format src tests && uv run ruff check --fix src tests`
- CLI: `uv run charswap --help`

## Layout
- `src/character_swap/providers/` is the ONLY code that calls the network.
- `stages/` = one pipeline step per file; `media/` = local video/audio work.
- Jobs live in `runs/<id>/` with `state.json` (resumable).

## Hard rules
- Never auto-retry a paid request. Persist provider IDs before waiting.
- Every paid action shows an estimate and needs confirmation.
- Tests never call paid APIs (use `tests/fakes.py`); live tests are `@pytest.mark.live`.
- Stage files by name; never `git add -A`. No attribution lines in commits.
- Never copy anything from `_legacy/` assets or mention the original author's brands.

## Docs (read on demand)
Spec: `docs/superpowers/specs/` · Plans: `docs/superpowers/plans/` · Journal: `activity.md` · Checklist: `docs/scratchpad.md` · UI guide: `docs/ui-ux-best-practices.md`
