# Click-through app: design

Date: 2026-11-13. Status: draft for Frank's review (revision 2, after the spec review).
Plan 3a of the v1 design.

## 1. Goal

A local web app that does a whole character swap by clicking, built to **explain the process
on camera** for the YouTube video. Viewers watch it, then clone the repo and run the same app
themselves. Later, the step flow and the explanation texts are reused in Dolly (a JavaScript
app), with BytePlus behind it; this app's code is not ported.

Success means Frank can film, with no terminal on screen:
upload a clip and a photo → watch the preparation fill in → compare before/after → approve →
pay for a draft → compare → pay for a final → download.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| A1 | Full app: every step is a button (not a viewer beside typed commands). | Frank chose it; the video shows the process, not a terminal. |
| A2 | Step by step: a home page plus 5 screens, "Step N of 5" at the top; earlier steps can be revisited. | Easy to film and follow. |
| A3 | Buttons only for WaveSpeed Seedance jobs in `depth` or `depth_mesh` mode, one person. Every other job (PiAPI, Wan/`direct`, `face_mesh`, two people) is shown read-only. | One clear path on camera; WaveSpeed is the default and easiest sign-up. |
| A4 | Keys stay in `.env`, edited by hand. The app re-reads `.env` on each request, shows a clear "missing key, get it here" notice and disables paid buttons. | Setup is explained in the README and the Claude skill (next plan). No key-writing code. |
| A5 | Long-running work (prepare, resume, repair, draft, final) runs as the existing `charswap` commands in a child process. Quick actions (approve, save the prompt) call the same Python functions the CLI uses. | Every tested money-safety rule is reused; work survives a closed browser or a stopped server. |
| A6 | **The child process enforces the confirmed price** (`--confirmed-usd`, §4.3). The page's price is only a display; the command refuses unless what it is about to charge equals what the person confirmed. | A server-side snapshot can go stale before the child runs. |
| A7 | A cross-process lock per job: `fcntl.flock` on `runs/<id>/run.lock`, taken by every command that changes a job. | The app can start commands from several tabs or clicks; today's lock is per process only (deferred P11). The OS frees a flock when the process dies, so crashes never leave a stuck lock. |
| A8 | One before/after "wipe" player per step: two videos in sync, a draggable divider. | Frank wants to see the original against each transformation. |
| A9 | Little text: one short line per step plus small technique tags (SAM 3, Depth Anything, …). Deeper explanation goes in the YouTube edit. | Frank: "less description". |
| A10 | All on-screen step texts live in one file, `web/steps.py`. | Dolly can copy the wording; clears the deferred "web/sections.py" item. |
| A11 | Provider names stay out of the main view; they appear only in the small price line ("about $0.88 · WaveSpeed"). | The app explains the process, not the vendors. |
| A12 | Optional "describe the person" text on upload, added to the prompt. Also in the CLI: `prepare --describe "…"`. | The live tests showed that describing face shape and hair improves likeness. |
| A13 | A "Look" choice on upload: **Better lip sync** (`depth_mesh`, default) or **Better likeness** (`depth`). | The live tests showed this trade-off; it is one radio button. |
| A14 | The app replaces the read-only viewer. New command `charswap app`; `charswap view` stays as an alias. Same port (8770), localhost only. | One web UI to maintain. |
| A15 | The app never deletes or edits provider records, claim markers or result files. Situations that need that ("needs your decision", §4.4) show the CLI's instructions instead of a button. | Those are exactly the cases where a click could buy something twice. |

## 3. Screens

Every screen has the step bar (`1 Upload · 2 Prepare · 3 Check · 4 Draft · 5 Final`), one
short line of text, technique tags where they apply, and one main button. Paid buttons show the
price; clicking opens a confirmation ("Charge about $0.88 on WaveSpeed?") before anything runs.

### Home
- One-line "how it works" strip: your clip → hide the person → keep the voice for lip sync →
  show the AI your photo → AI redraws every frame → original sound back.
- **New swap** button.
- List of past swaps: thumbnail, clip name, current step, money spent (as `list_jobs` today,
  which never hashes files). Read-only jobs (A3) carry a "made with the command line" label.
- Total spent across all swaps.
- Missing `REPLICATE_API_TOKEN` or `WAVESPEED_API_KEY`: a notice naming the variable, the
  `.env` file and where to get the key (`config.WHERE_TO_GET`); **New swap** is disabled.

### Step 1 · Upload
- Two drop zones: clip (one person, 4–30 s, mp4/mov) and photo (jpg/png/webp, face clearly
  visible).
- Optional "Describe the person", at most 300 characters (A12).
- Look: Better lip sync (default) / Better likeness (A13).
- Tick box: "I have the right to use this face. If it's a made-up character, tick it." Required.
- Button: **Prepare · about $0.06** (`estimate_prepare(mode)`, the CLI's figure).
- Under it, once the browser has read the clip's length from the video's metadata: "Draft later:
  about $X". The browser computes whole seconds (rounded down, as normalize cuts), at least 4,
  times the 480p price per second of clip that the page receives from the server (WaveSpeed
  bills input plus output seconds, so $0.11 + $0.11 = $0.22). It is a hint only; the real draft
  price is confirmed at step 4.
- Free checks on the server before anything starts: both files present, each at most 200 MB,
  the clip opens in PyAV and its whole-second length is within `GENERATOR_LIMITS["seedance"]`,
  the photo passes `faces.check_photo`, the tick box is set, both keys present. Errors show next
  to the field and nothing is started.

### Step 2 · Prepare
- Fills in live (about 2 minutes). Tabs, each with the wipe player against the normalized
  original (`original.mp4`):
  - **Depth**: `depth.mp4`.
  - **Cut-out**: the depth composite (`view_model.composite_name`: `depth-composite-silent.mp4`
    in depth_mesh, `composite-silent.mp4` in depth).
  - **Face lines** (lip-sync look only): `composite-silent.mp4`.
  - **What the AI gets**: `prepared.mp4`.
  - **Voice**: two audio players (`vocals.wav`, `vocals-plus3.wav`) and the transcript.
- A tab is greyed out until its file exists; a running stage shows a spinner.
- Tags: FFmpeg, Demucs, Rubber Band, Whisper, GPT-4o mini (vision), Depth Anything, SAM 3,
  MediaPipe Face Mesh (each on the tab it belongs to).
- When every prep stage is done: **Next: check it**.

### Step 3 · Check
- Wipe player: original ↔ what the AI gets.
- **Looks good, approve** unlocks only after "what the AI gets" has played to the end once on
  this page (tracked in the browser; hint: "watch it to the end first").
- Secondary: **Redo the cut-out** (paid, `charswap repair`, with its price) and **Start over**
  (back to step 1; the job stays in the list).
- The page is rendered with the current review fingerprint (`review.fingerprint`, computed on
  this page render only). Approving calls `review.decide(approved=True, note="approved in the
  app", fingerprint=<that value>)` under the job lock. If the files changed, the existing "changed
  since you looked" error is shown with a reload link.

### Step 4 · Draft
- The prompt in an editable box. It shows `prompt.txt` if it exists, else `default_prompt(job)`.
  Rendering never writes. **Save** validates (`validate_prompt`, `check_placeholders` for
  WaveSpeed) and writes `prompt.txt` under the job lock; **Reset** writes `default_prompt`.
- Button: **Make draft · about $0.88** (§4.3).
- While running: elapsed timer and "usually about 4 minutes". Leaving the page does not stop it.
- When done: wipe player original ↔ `draft.mp4` (original soundtrack), and **Next: final**.
- **Make another draft** appears only when the CLI would remake it: the prompt was saved with
  different text since the draft (`draft_prompt_changed`) or the approved inputs changed
  (`stale_draft`). Its confirmation says: "The current draft (and final, if any) are kept as
  draft-old-* / final-old-*." When a final is still in progress, the button is replaced by the
  CLI's "final still in progress" message.

### Step 5 · Final
- Line: "The final is a new take, so it will look a little different from the draft."
- Buttons: **720p · about $1.76** and **1080p · about $4.40** (§4.3).
- When done: wipe player original ↔ `final.mp4`, **Download**, and the total spent on this swap.
- Once a final is started or done, the other resolution's button is hidden with the CLI's
  message (`refuse_another_final` / `final_resolution`).

### Read-only jobs (A3)
Same screens and players for whatever files exist, no buttons, and one line: "Made with the
command line. Continue there: `charswap status <id>`."

## 4. How it works

### 4.1 Running work
- The server starts work as `[*runner, <command>, …]`, where `runner` defaults to
  `[sys.executable, "-m", "character_swap"]` (new `character_swap/__main__.py` calling
  `cli.main`). The child gets its own session (`start_new_session=True`), so it outlives the
  server. Its stdin is `subprocess.DEVNULL` and its working directory is the repo root. Output
  is appended to `runs/<id>/app.log`, starting with a `=== <command> <time> ===` line, so the
  "last lines" always belong to the latest run. The server reaps its children (`Popen.poll` on
  each progress request and on shutdown).
- The page never parses output. It polls `GET /api/jobs/{id}` every 2 s. The endpoint stays
  cheap (no hashing): it adds the lock holder (from `run.lock`, §4.2), the saved review
  (`review.json`'s `approved` and `fingerprint`, read but not recomputed), the generation record
  statuses, and the step's "needs your decision" text (§4.4).
- **New swap**: `POST /jobs` saves the uploads to `runs/_incoming/<random>/clip.<ext>` and
  `photo.<ext>` (user file names are never used as paths) and starts
  `charswap prepare <clip> --character <photo> --mode <look> [--describe …] --yes
  --confirmed-usd <price> --id-file <path>`. It returns at once with a start id; the page
  shows "Starting…" and polls `GET /api/starts/{start}` until either:
  - the id file exists → redirect to step 2; or
  - the child exited without one → show the last lines of the child's log as the error.

  There is no timeout and the server never kills a prepare. `prepare` writes the id file right
  after it has created the job and taken its lock, and only then copies on. `_incoming/<random>`
  is deleted when the child exits. If the server restarts mid-start, the job simply appears on
  the home list.
- `--yes` answers the rights question (asked on the page); the cost question is replaced by
  `--confirmed-usd`.

### 4.2 Lock (A7)
- `runs/<id>/run.lock` is opened and locked with `fcntl.flock(LOCK_EX | LOCK_NB)` for the whole
  run; the file holds `{pid, command, started_at}` for display. The OS releases it when the
  process exits or dies. (macOS and Linux only; the tool already targets those.)
- Taken by `prepare` (once the job exists), `resume`, `repair`, `generate`, `review`, and
  `prompt` (when it writes). A command that cannot get it exits 2 with "This swap is busy
  (<command> since <time>). Try again when it finishes." Nothing is charged.
- The app's in-process approve and prompt save take the same lock.
- `run.lock` is never deleted, because deleting a flock file while it is held would let two
  processes lock two different files. The holder overwrites its content after locking.
- The server never takes the lock just to check it, because that could make a starting child
  fail with "busy". "Running" on the page means either:
  - the server has a live child for this job (`Popen.poll() is None`, after reaping); or
  - the pid in `run.lock` is alive (`os.kill(pid, 0)`), for commands started from a terminal.
    Because `run.lock` keeps its last pid, the content also records the process start time; a
    live pid whose start time differs is a reused pid and counts as not running.
- To absorb any brief overlap, a command retries the non-blocking lock for up to 2 s before
  reporting "busy".
- When nothing is running, §4.4 decides what to offer.
- The existing paid-create claim markers stay; the lock is an extra layer.

### 4.3 Paid actions and `--confirmed-usd` (A6)
- New CLI option on `prepare`, `resume`, `repair` and `generate`: `--confirmed-usd X`.
  - The command computes the charge it is about to make, exactly where it shows the estimate
    today (the same request object for draft and final).
  - Amounts are compared in whole micro-dollars (`round(usd * 1_000_000)`).
  - If it is about to charge and the amount is not X → exit 2, "The price is now about $Y, not
    $X. Nothing was charged."
  - `X = 0` means "no new charge allowed": any charge → exit 2, same message.
  - No charge needed (for example a draft resuming a saved prediction) → it runs, whatever X is.
  - With `--confirmed-usd`, the cost question is never asked.
- One shared function per command returns "what this would charge now", for example
  `planned_charge(job, command, resolution) -> Estimate | None`. The CLI uses it for its
  question and for `--confirmed-usd`; `GET /api/jobs/{id}/estimate?action=…` uses it for the
  dialog. The two can't disagree, and the child has the last word.
- Every paid POST (`/jobs`, `/jobs/{id}/repair`, `/draft`, `/final`, `/retry`) needs
  `confirm_usd`, the price the dialog showed. The server passes it on as `--confirmed-usd` and
  starts the child.
- Before starting, the server runs the CLI's free refusals for that command, so the message
  appears at once. The generate refusals (another final, a final still in progress, a stale
  final, the approval check, an unconfirmed marker, prompt names) are moved from `cli._generate`
  into one `pipeline.generate_refusal(job, stage, resolution) -> str | None` used by both. The
  child repeats every check anyway.

### 4.4 Try again and "needs your decision"
The step's state decides what the page offers. It never deletes anything (A15).

**While the job is running (§4.2), the page shows only "running".** Claim markers and
records come and go during a healthy run, so the table below applies only when nothing is
running:

| Situation | Detected by | Page shows |
|---|---|---|
| Preparation not finished | not every prep stage done | **Try again** → `resume <id>` with `planned_charge(resume)`: "Continue (no new charge)" when it is 0, otherwise the price dialog. Covers a failed stage, a stopped prepare, and a stopped repair (repair resets `composite`, `embed`, `face_mesh`). |
| Draft/final can resume for free | `pipeline.resumes_without_charge` (a saved record still running or succeeded, or a downloaded raw file) and not expired | **Continue (no new charge)** → `generate --draft|--final --confirmed-usd 0`. |
| A request sent but never confirmed (claim marker left) | `saved_request_unconfirmed` | No button. The CLI's `UNCONFIRMED` text: check the WaveSpeed dashboard, then follow the steps. |
| Provider says the job failed (failed record) | record status `failed` | No button. The provider's error plus the CLI's text ("delete … and run again" is a new charge; do it from the command line). |
| Result link expired before download | a structured `expired: true` on the stage (new; no text matching). `_tracked` sets it when an `OutputExpired` is anywhere in the error's `__cause__` chain (the generator wraps it in `ProviderError`, and `_wrap` wraps that in `StageError`) | No button. The CLI's text. |
| A prep stage's Replicate record is a leftover claim marker or `failed` | the prep records in the view-model cost table | No button. `resume`'s own "delete … to try again (this costs money)" text. (Otherwise Try again would fail the same way every time.) |
| Free refusal (busy, not approved, price changed, …) | the child's exit 2 or `generate_refusal` | The message, and the button again once the cause is gone. |

**Redo the cut-out** is hidden while `rgb-repair/sam-prediction.json` is an unconfirmed marker
or a running prediction, because `repair_with_rgb` deletes `rgb-repair/` and would rebuy it.
Otherwise deleting a finished earlier repair is the CLI's existing, confirmed behaviour and the
one accepted exception to A15.

### 4.5 Routes
- **GET:** `/` home, `/new` step 1, `/jobs/{id}` (redirects to the job's current step),
  `/jobs/{id}/step/{n}`, `/jobs/{id}/files/{name}`, `/api/jobs/{id}`,
  `/api/jobs/{id}/estimate`, `/api/starts/{start}`.
- **POST:** `/jobs` (upload + prepare), `/jobs/{id}/approve`, `/jobs/{id}/repair`,
  `/jobs/{id}/prompt` (save / reset), `/jobs/{id}/draft`, `/jobs/{id}/final`, `/jobs/{id}/retry`.
- Read-only jobs (A3): every POST returns 409 "Continue this job with the command line."

### 4.6 Security (a localhost app that can spend money)
- Binds to 127.0.0.1; `TrustedHostMiddleware` stays.
- Every POST needs:
  - a per-launch random token, from a `<meta>` tag, sent as a header;
  - **and** an `Origin` of `http://127.0.0.1:<port>` or `http://localhost:<port>`.

  Otherwise it gets 403. This stops other websites from triggering a paid step. A page left
  open across a server restart has an old token: the 403 says "The app was restarted. Reload
  the page."
- Uploads: a request whose `Content-Length` is over 2 × 200 MB plus 1 MB is refused before it
  is read. Each file is counted while it is written to disk and refused past 200 MB. The clip
  must open in PyAV and the photo in Pillow.
- CSP and the other security headers stay as they are; scripts stay in `static/`.
- GET routes never change a job.
- New dependency: `python-multipart` (FastAPI's form uploads).

### 4.7 The wipe player
- Two `<video>` elements stacked in one box; the top one is clipped with `clip-path` at the
  divider. A range input (or dragging on the video) moves the divider; arrow keys work.
- One play/pause and one seek bar drive both. The second video follows the first's
  `currentTime`, corrected when they drift by more than 0.1 s. Muted by default; the sound toggle
  plays the "after" side (draft and final carry the original sound).
- Both videos scale to the same box (`object-fit: contain`), so different resolutions line up.
- With `prefers-reduced-motion`, the two videos are shown side by side instead.

### 4.8 Texts (A9, A10)
- `web/steps.py` holds, for each step and tab: the title, a one-line explanation, the technique
  tags (a name plus a one-sentence tooltip), the button labels, and the file each tab shows.
  Templates read only from it.
- The viewer's `SECTIONS` table and its long "why" texts are removed. `PAID_RECORD` and
  `cost_rows`, which are derived from it today, move to a small cost table in
  `web/view_model.py` that still feeds "money spent".

### 4.9 Person description (A12)
- `prepare --describe "…"` (Seedance only; refused for Wan) is cleaned with `prompts._clean`,
  which removes "@" and quotes, so a description can't add input names. It is trimmed to 300
  characters and stored as `character_description` in `state.json`.
- `build_prompt` adds one line: `The character in @Image 1 looks like this: <text>.`, using
  the provider's input names. The existing length limits apply; the transcript is truncated
  first.

## 5. Errors

- Free problems (missing file, bad photo, clip length, missing key, prompt names) show on the
  screen where they happen, in plain words, before anything is started or charged.
- A child that exits non-zero: the page shows its message (the last lines of `app.log`) and §4.4
  decides what to offer.
- Ambiguous paid answers keep the CLI behaviour (marker kept, "check the WaveSpeed dashboard"),
  shown on the page as "needs your decision".

## 6. Out of scope (this plan)

- Next plan (3b): the Claude skill, README and setup docs, removing `_legacy/`, a Dolly
  waitlist link.
- Not planned: PiAPI or Wan actions in the app, typing keys in the app, two people, face library,
  accounts, payments, sharing, Windows, mobile layout beyond "doesn't break" at 375 px.
- `CLAUDE.md` is updated: `web/` is no longer read-only; GET routes stay read-only.

## 7. Testing

- **Unit:**
  - `steps.py` covers every step and tab, and the file names per mode.
  - Lock: a second holder is refused; it is released on error and on process exit.
  - `--id-file` is written after the lock.
  - `--describe`: stored in state and added to the prompt, with cleaning, the length limit, the
    provider's names, and refused for Wan.
  - `--confirmed-usd`: match, mismatch, 0 with a charge, 0 with a resume, micro-dollar rounding.
  - `planned_charge` and `generate_refusal` give the same answers the CLI gave before the move.
- **Web** (FastAPI TestClient, a fake runner that records commands, no network):
  - every route works;
  - a POST is refused without the token, with a wrong Origin, on a read-only job, without
    `confirm_usd`, and while locked;
  - approve is refused with a stale fingerprint;
  - prompt save refuses bad names;
  - each §4.4 situation shows the right button or message;
  - the step redirect follows the job's progress;
  - a GET never writes, and `/api/jobs/{id}` never hashes.
- **Integration:** the real CLI runs in a child process through a test entry script,
  `tests/fake_cli.py`.
  - The script puts `tests/` on `sys.path` (the tests import `from fakes import …`; there is no
    `tests/__init__.py`).
  - It stubs `pipeline.pitch_vocals` itself, as the `no_rubberband` fixture does.
  - It calls `cli.main` with a `services_factory` built from fakes in `tests/fakes.py`.
    `FakeDetector` moves there from `test_pipeline.py`. The fake Replicate and `FakeWaveSpeed`
    keep their state in files under the test's temporary folder, so it survives across child
    processes.
  - The app is created with `runner=[sys.executable, "tests/fake_cli.py"]` and the repo root as
    the working directory.
  - One full click-through (prepare → approve → draft → final) checks the files, the costs, and
    that each paid create happened exactly once.
- The wipe player and the "watched to the end" gate are checked in the browser: by me with
  Playwright during the build, and in Frank's checklist. No new JS test dependency.
- Coverage gate stays at 85 %; ruff clean.
- **Frank's browser checklist (before filming):**
  - each step on a real job, and the wipe player on every tab;
  - the approve gate and the price dialog;
  - a refresh mid-draft, and closing the browser mid-draft;
  - a missing key.
