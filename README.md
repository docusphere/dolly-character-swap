# Character Swap

Swap a person in a short video for your own character while keeping the original motion, timing and soundtrack. Runs locally; uses Seedance 2.5 (via PiAPI) or the open-source Wan 2.2 Animate (via Replicate).

**Status:** work in progress. Both paths are built: Wan (one live draft so far) and Seedance on PiAPI (not yet tested live). The design is in [docs/superpowers/specs/](docs/superpowers/specs/2026-10-22-character-swap-v1-design.md).

Only use faces and footage you have the rights to use.

## Try it with Wan (open source, on Replicate)

You need [uv](https://docs.astral.sh/uv/) and a [Replicate API token](https://replicate.com/account/api-tokens) in `.env` as `REPLICATE_API_TOKEN=...`. (Rubber Band, `brew install rubberband`, is only needed for the Seedance modes.)

Put your clip and your character photo in the `inputs/` folder (git-ignored, so third-party footage is never committed).

1. `uv sync`
2. `uv run charswap prepare inputs/clip.mp4 --generator wan --mode direct --character inputs/me.jpg`
   Use a clip of up to 30 s with **one** person, and a clear, well-lit photo of **one** person. Preparing is free for Wan (nothing is sent to a paid service), so it asks nothing. (`--language` is for Seedance modes only; Wan has no transcript step.)
3. `uv run charswap view` opens the viewer so you can watch the normalized clip and your photo. It keeps running until you press Ctrl-C in that terminal. Then copy the fingerprint from `uv run charswap status <id>` and approve: `uv run charswap review <id> --approve --note "watched all" --fingerprint <fp>`. `status` also tells you the next step.
4. `uv run charswap generate <id> --draft` makes a 480p test (about $0.02 per second of video). It shows the estimate and asks you to confirm before any money is spent (`--yes` skips the question).
5. Watch the draft in the viewer. Happy with it? `uv run charswap generate <id> --final` makes the 720p version with the same seed (about $0.05 per second), again with a cost question.

The final is only allowed for the exact inputs you watched in the draft. The draft becomes out of date only when the clip or photo files change (approving the same files again does not). There is no command to change the photo: to use a different one, start a new job with `uv run charswap prepare … --character <new photo>` (recommended) rather than editing files under `runs/`. An out-of-date draft can be remade (the cost is asked again and the old files are kept as `draft-old-*` / `final-old-*`).

The result is `runs/<id>/final.mp4`, with your clip's original soundtrack put back unchanged. If the clip's sound and picture already differ by more than about 0.4 s, it is refused before you pay. If Replicate is busy ("Replicate is busy..."), the tool waits and tries again by itself; it never charges twice. If a step stops, run the same command again: saved requests are resumed, never paid twice.

Prices are estimates (Wan's price is not confirmed yet); check replicate.com billing for exact charges.

## Try it with Seedance (best quality, real faces, on PiAPI)

You need a PiAPI account on the **Creator plan or higher** (saved faces and uploads), with
`PIAPI_API_KEY` in `.env`, plus `REPLICATE_API_TOKEN` for preparation.

```bash
uv run charswap face add me me.jpg                         # once, free: PiAPI checks the photo
uv run charswap prepare clip.mp4 --mode depth --face me    # ~$0.06: the depth disguise
# watch it in `uv run charswap view`, then approve with the command shown there
uv run charswap prompt <id>                                # optional: edit prompt.txt
uv run charswap generate <id> --draft                      # 480p, shows the price, asks first
uv run charswap generate <id> --final                      # 1080p upgrade of that draft
uv run charswap generate <id> --final --resolution 720p    # or: cheaper fresh 720p run
```

Prices for a 12 s clip, estimated: draft about $3, 1080p final about $16, 720p final about
$7 (PiAPI also bills the input video). Test with 4–5 s clips first.

Two people in the clip: `--subject` tells SAM what to cut out (default "person" = everyone),
and `--face2` / `--character2` gives the second person their own character. Say in the
prompt who becomes @image1 and who becomes @image2.

Clips are trimmed to whole seconds (PiAPI works in whole seconds); the original sound is
cut to match. `--character IMAGE` is only for characters that are not real people (PiAPI
rejects real faces sent that way); use a saved face for a real person. Only add faces you
have the right to use.

## License

[MIT](LICENSE)
