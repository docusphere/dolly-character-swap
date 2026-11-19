---
name: character-swap
description: Use when the user wants to put themselves (or a character) into a video clip, swap a person in a clip, swap one person in one shot of a longer scene, or check on, fix or finish a swap made with this repo's `charswap` tool. Triggers on "swap me into this clip", "put my face in this video", "character swap", "make the draft", "make the final".
---

# Character Swap

Runs this repo's `charswap` tool for the user: their clip plus their photo becomes a video
where the person in the clip is replaced, with the original motion, timing and sound kept.
Every command is `uv run charswap ...` from the repo root. The README has the full details;
this file is the order of steps and the rules.

## Rules (never break these)

1. **Money:** never pass `--yes` to `prepare`, `resume`, `repair` or `generate` unless the
   user said yes to that exact step and price in this conversation. Run the command without
   `--yes`, show the user the price it prints, and let them answer. Never run a paid command
   twice to "retry": if one stops or fails, run `uv run charswap status <id>` and then
   `uv run charswap resume <id>`, which re-checks saved requests instead of buying again.
2. **Rights:** only faces and footage the user has the right to use. A real person's face
   needs that person's consent. Do not help put a real person (celebrity, politician,
   someone they know) into a clip without their consent, and never answer the rights
   question in `prepare` on the user's behalf.
3. **Approval:** never run `charswap review --approve` until the user has watched the
   prepared clip in the app and told you it looks right.
4. **Keys:** the user types their own API keys into `.env`. Never ask them to paste a key
   into the chat, and never print `.env`.

## Steps

1. **Setup check (free):** `uv run charswap doctor`. If something is missing, tell the user
   what and where to get it (see README: `uv`, Rubber Band, `REPLICATE_API_TOKEN`,
   `WAVESPEED_API_KEY`). The user copies `.env.example` to `.env` and fills it in.
2. **Inputs:** ask for the clip and the photo and put them in `inputs/` (git-ignored, never
   committed). Good results need:
   - a clip of 4–5 s for the first try, with one person (or `--from/--to` for one shot);
   - a head-and-shoulders photo, taken from at least arm's length, against a plain
     background, with good light. Close selfies stretch the face.
3. **Prepare (about $0.06):**
   `uv run charswap prepare inputs/clip.mp4 --character inputs/photo.jpg`
   - Defaults: Seedance on WaveSpeed, `depth_mesh` mode (best lip sync).
   - `--mode depth` hides the original face shape more (better likeness, guessed lips).
   - One shot of a longer scene: add `--from S --to S --subject "man in a red coat"`.
   - Cheap open-source route: `--generator wan --mode direct` (free to prepare, weaker
     likeness, copies the photo's background and clothes).
   It prints a job id. Use it in every later command.
4. **Review:** `uv run charswap app` opens the walkthrough in the browser (tell the user it
   keeps running until Ctrl-C). The user watches the clip. If the cut-out is wrong, offer
   `uv run charswap repair <id>`. When they are happy, get the fingerprint from
   `uv run charswap status <id>` and run
   `uv run charswap review <id> --approve --note "<what they checked>" --fingerprint <fp>`.
5. **Prompt (optional, free):** `uv run charswap prompt <id>` shows `runs/<id>/prompt.txt`.
   Describing the person's face shape and hair in a sentence helps likeness. Keep the
   placeholders the file already uses.
6. **Draft (paid):** `uv run charswap generate <id> --draft`. 480p, about $0.22 per second
   of clip on WaveSpeed, about 3 minutes for 4 s. Result: `runs/<id>/draft.mp4`.
7. **Final (paid, only if they want it):** `uv run charswap generate <id> --final`
   (720p, about $0.44 per second) or add `--resolution 1080p` (about $1.10 per second).
   On WaveSpeed the final is a new take, so it looks a little different from the draft;
   say so before they pay. Result: `runs/<id>/final.mp4`.

## When something goes wrong

- SAM finds nothing: use a plain subject such as `person`, not `man`.
- The setting description is refused (common on film stills):
  `uv run charswap resume <id> --setting "a dim office with a desk and a window"` (free).
- Scene swaps showing the original's hair or too much AI background around the head:
  `uv run charswap edge <id> wide` (or `tight`, `normal`; add `--final` for the final). Free.
- Two people in one clip need `--provider piapi` (`--face2` / `--character2`) for now.
- Anything else: `uv run charswap status <id>` shows each step and the next command.

Prices are estimates. The provider's billing page has the real charge.
