# Character Swap

Swap a person in a short video for your own character while keeping the original motion, timing and soundtrack. Runs locally; uses Seedance 2.5 (via PiAPI) or the open-source Wan 2.2 Animate (via Replicate).

**Status:** work in progress. The open-source Wan path works end to end (not yet tested live); Seedance (PiAPI) is next. The design is in [docs/superpowers/specs/](docs/superpowers/specs/2026-10-22-character-swap-v1-design.md).

Only use faces and footage you have the rights to use.

## Try it with Wan (open source, on Replicate)

You need [uv](https://docs.astral.sh/uv/), Rubber Band (`brew install rubberband`) and a [Replicate API token](https://replicate.com/account/api-tokens) in `.env` as `REPLICATE_API_TOKEN=...`.

Put your clip and your character photo in the `inputs/` folder (git-ignored, so third-party footage is never committed).

1. `uv sync`
2. `uv run charswap prepare inputs/clip.mp4 --generator wan --mode direct --character inputs/me.jpg`
   Use a clip of up to 30 s with **one** person, and a clear, well-lit photo of **one** person. Add `--language es` (or another language code) if the clip is not in English; the default is to detect it. Preparing costs a few cents and asks first.
3. `uv run charswap view` opens the viewer so you can watch the normalized clip and your photo. It keeps running until you press Ctrl-C in that terminal. Then copy the fingerprint from `uv run charswap status <id>` and approve: `uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>`. `status` also tells you the next step.
4. `uv run charswap generate <id> --draft` makes a 480p test (about $0.02 per second of video). It shows the estimate and asks you to confirm before any money is spent (`--yes` skips the question).
5. Watch the draft in the viewer. Happy with it? `uv run charswap generate <id> --final` makes the 720p version with the same seed (about $0.05 per second), again with a cost question.

The final is only allowed for the exact inputs you watched in the draft. If you change the clip, photo or review, the draft is marked out of date and you can remake it (the cost is asked again and the old files are kept as `draft-old-*` / `final-old-*`).

The result is `runs/<id>/final.mp4`, with your clip's original soundtrack put back unchanged. If the clip's sound and picture already differ by more than about 0.4 s, it is refused before you pay. If Replicate is busy ("Replicate is busy..."), the tool waits and tries again by itself; it never charges twice. If a step stops, run the same command again: saved requests are resumed, never paid twice.

Prices are estimates (Wan's price is not confirmed yet); check replicate.com billing for exact charges.

## License

[MIT](LICENSE)
