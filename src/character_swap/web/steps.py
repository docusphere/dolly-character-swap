"""Every word the app shows for a step: titles, one-line explanations, technique tags, buttons.

Templates read their texts only from here, so the wording can be copied to another app.
Plain English, one short line per step, no em dashes (docs/ui-ux-best-practices.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tag:
    name: str  # the technique, e.g. "SAM 3"
    tip: str  # one sentence, shown as a tooltip


@dataclass(frozen=True)
class Tab:
    id: str
    title: str
    line: str
    tags: tuple[Tag, ...]
    stages: tuple[str, ...]  # the tab is ready once all of these are done
    after: str  # the "after" side of the wipe; "@composite" = view_model.composite_name; "" = audio
    modes: tuple[str, ...]  # the modes that show this tab


@dataclass(frozen=True)
class Step:
    number: int
    id: str
    title: str  # the step bar's label
    heading: str
    line: str
    tags: tuple[Tag, ...] = ()


FFMPEG = Tag("FFmpeg", "Sets the clip to 24 frames a second and puts the voice back in.")
DEMUCS = Tag("Demucs", "Separates the voice from the music and background noise.")
RUBBER_BAND = Tag("Rubber Band", "Raises the voice 3 semitones, so the AI hears the words only.")
WHISPER = Tag("Whisper", "Writes down the spoken words for the prompt.")
GPT_VISION = Tag("GPT-4o mini (vision)", "Describes the room, never the people, for the prompt.")
DEPTH_ANYTHING = Tag(
    "Depth Anything", "Colors everything by distance: the motion stays, the face goes."
)
SAM3 = Tag("SAM 3", "Finds the person in every frame and cuts them out.")
FACE_MESH = Tag("MediaPipe Face Mesh", "Tracks 478 points on the face for lip sync.")
SEEDANCE = Tag("Seedance 2.5", "Redraws every frame of the clip with your character.")

STEPS: tuple[Step, ...] = (
    Step(
        1,
        "upload",
        "Upload",
        "Upload",
        "A short clip of one person, and a photo of who they become.",
    ),
    Step(
        2,
        "prepare",
        "Prepare",
        "Prepare",
        "We hide who is in the clip, but keep how they move and what they say.",
    ),
    Step(3, "check", "Check", "Check", "This is all the AI will see. Watch it to the end."),
    Step(4, "draft", "Draft", "Draft", "A cheap 480p test run.", (SEEDANCE,)),
    Step(
        5,
        "final",
        "Final",
        "Final",
        "The final is a new take, so it will look a little different from the draft.",
        (SEEDANCE,),
    ),
)

DEPTH_MODES = ("depth", "depth_mesh")
SEEDANCE_MODES = ("depth", "face_mesh", "depth_mesh")
TABS: tuple[Tab, ...] = (
    Tab(
        "depth",
        "Depth",
        "Everything becomes colors by distance, so nobody can tell who it is.",
        (DEPTH_ANYTHING,),
        ("depth",),
        "depth.mp4",
        DEPTH_MODES,
    ),
    Tab(
        "cutout",
        "Cut-out",
        "Only the person is swapped for depth; the real room stays.",
        (SAM3,),
        ("masks", "composite"),
        "@composite",
        DEPTH_MODES,
    ),
    Tab(
        "face",
        "Face lines",
        "Face lines on top keep every lip movement.",
        (FACE_MESH,),
        ("face_mesh",),
        "composite-silent.mp4",
        ("face_mesh", "depth_mesh"),
    ),
    Tab(
        "prepared",
        "What the AI gets",
        "The motion and a raised voice, but not who it was.",
        (FFMPEG, GPT_VISION),
        ("embed",),
        "prepared.mp4",
        SEEDANCE_MODES,
    ),
    Tab(
        "voice",
        "Voice",
        "The voice is lifted out and raised 3 semitones. The words stay the same.",
        (DEMUCS, RUBBER_BAND, WHISPER),
        ("separate", "pitch", "transcribe"),
        "",
        SEEDANCE_MODES,
    ),
)

# Home page: how a swap works, in six short steps.
HOW_IT_WORKS: tuple[str, ...] = (
    "Your clip",
    "Hide the person",
    "Keep the voice for lip sync",
    "Show the AI your photo",
    "AI redraws every frame",
    "Original sound back",
)

# Upload choices: the "Look" radio button (value -> mode, label, hint).
LOOKS: tuple[tuple[str, str, str], ...] = (
    ("depth_mesh", "Better lip sync", "Face lines guide the mouth."),
    ("depth", "Better likeness", "The face looks more like your photo."),
)
RIGHTS = "I have the right to use this face. If it's a made-up character, tick it."
DESCRIBE = "Describe the person (optional)"
DESCRIBE_HINT = "Face shape, hair, age. It is added to the prompt."

# Buttons and short messages.
NEW_SWAP = "New swap"
PREPARE = "Prepare"
NEXT_PREPARE = "Next: prepare"
NEXT_CHECK = "Next: check it"
APPROVE = "Looks good, approve"
WATCH_FIRST = "Watch it to the end first."
REPAIR = "Redo the cut-out"
START_OVER = "Start over"
NEXT_DRAFT = "Next: draft"
MAKE_DRAFT = "Make draft"
ANOTHER_DRAFT = "Make another draft"
NEXT_FINAL = "Next: final"
DOWNLOAD = "Download"
TRY_AGAIN = "Try again"
CONTINUE_FREE = "Continue (no new charge)"
SAVE = "Save"
RESET = "Reset"
DRAFT_TIME = "Usually about 4 minutes. You can leave this page; it keeps going."
PREPARE_TIME = "About 2 minutes. You can leave this page; it keeps going."
KEPT_AS_OLD = "The current draft (and final, if any) are kept as draft-old-* / final-old-*."
READ_ONLY = "Made with the command line. Continue there: charswap status {id}"
DIRECT_PREPARE = "Wan copies the motion straight from your clip, so nothing is hidden."
WAITING = "Not made yet."
RUNNING = "Working on it…"
FILE_MISSING = "File missing."
NEW_RESULTS = "New results are ready."
REFRESH = "Refresh"
LAST_STOPPED = "The last command stopped:"
STALE_DRAFT = "This draft was made from files that are no longer approved."
NEEDS_DECISION = "Needs your decision."
DECISION_HOW = "Nothing will be bought again from here; do this from the command line:"
CHARGE = "Charge about {usd} on {provider}?"  # the price dialog's question
CHARGE_YES = "Yes, charge it"
CHARGE_NO = "Cancel"

# The before/after player's controls (the divider is labelled so it never looks like the seek bar).
COMPARE = "Compare"
SOUND = "Sound"  # a toggle: aria-pressed and its pressed style say whether it is on


def step(number: int) -> Step:
    """The step with this number (1 to 5)."""
    for item in STEPS:
        if item.number == number:
            return item
    raise ValueError(f"There is no step {number}.")


def tabs_for(mode: str) -> tuple[Tab, ...]:
    """The prepare tabs a job's mode shows, in page order."""
    return tuple(tab for tab in TABS if mode in tab.modes)
