# Character Swap

Swap a person in a short video for your own character while keeping the original motion, timing and soundtrack. Runs locally; uses Seedance 2.5 (via WaveSpeed, or PiAPI) or the open-source Wan 2.2 Animate (via Replicate).

**Status:** work in progress. Seedance on WaveSpeed is the default (three live drafts so far); Wan (one live draft) and Seedance on PiAPI (not yet tested live) also work. The design is in [docs/superpowers/specs/](docs/superpowers/specs/2026-11-12-wavespeed-seedance-design.md).

Only use faces and footage you have the rights to use.

## Try it with Seedance on WaveSpeed (best quality, real faces)

Sign up at [WaveSpeed](https://wavespeed.ai): pay per use, $1 of free credit, no
subscription. Put your key in `.env` as `WAVESPEED_API_KEY=...` (Dashboard > API Keys),
plus `REPLICATE_API_TOKEN` for preparation, and install Rubber Band
(`brew install rubberband`). You also need [uv](https://docs.astral.sh/uv/).

Put your clip and your character photo in the `inputs/` folder (git-ignored, so footage and
faces are never committed), then:

```bash
uv sync                                                      # once: installs the tool
uv run charswap prepare inputs/clip.mp4 --character inputs/me.jpg  # asks about rights, then ~$0.06
# watch it in `uv run charswap view`, then approve with the command shown there
uv run charswap prompt <id>                                  # optional: edit prompt.txt
uv run charswap generate <id> --draft                        # 480p, shows the price, asks first
uv run charswap generate <id> --final                        # a new 720p take
uv run charswap generate <id> --final --resolution 1080p     # or a new 1080p take
```

The default mode is `depth_mesh`: it turns the person into a colored silhouette with
face-tracking lines, so the new face comes from your photo while the lips follow the
original. It gave the best lip sync in tests.

Prices for a 4 s clip, estimated: draft about $0.88, 720p final about $1.76, 1080p final
about $4.40 (WaveSpeed bills the input video as well as the result, and at least 4 s). A
4 s draft takes about 3 minutes. Test with 4–5 s clips first.

WaveSpeed cannot upgrade the draft itself, so the final is a new take: it will look a little
different from the draft. A real person's face works as a plain photo; only use faces you
have the right to use (prepare asks). One person per clip for now; two people need
`--provider piapi`. Clips are trimmed to whole seconds; the original sound is cut to match
and put back unchanged. The draft is saved as `runs/<id>/draft.mp4` and the final as
`runs/<id>/final.mp4`.

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

## Other providers

### Seedance on PiAPI (saved faces, two people, the 1080p draft upgrade)

You need a PiAPI account on the **Creator plan or higher** (saved faces and uploads), with
`PIAPI_API_KEY` in `.env`, plus `REPLICATE_API_TOKEN` for preparation.

```bash
uv run charswap face add me me.jpg                         # once, free: PiAPI checks the photo
uv run charswap prepare clip.mp4 --provider piapi --face me # ~$0.06, default mode depth_mesh
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
cut to match. With `--provider piapi`, `--character IMAGE` is only for characters that are
not real people (PiAPI rejects real faces sent that way); use a saved face for a real
person. Only add faces you have the right to use.

## License

[MIT](LICENSE)
