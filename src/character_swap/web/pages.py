"""What each of the five step screens shows, built from a job folder.

Pure: no HTTP, no templates, and never a write (GET routes never change a job).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from character_swap.config import WHERE_TO_GET, Settings
from character_swap.costs import total_spend
from character_swap.jobs import Job
from character_swap.pipeline import PROMPT_FILE, default_prompt, generator_key
from character_swap.review import ReviewError, fingerprint
from character_swap.web import steps
from character_swap.web.view_model import (
    STAGES_BY_MODE,
    JobSummary,
    ViewError,
    combine,
    completed_cost,
    composite_name,
    cost_rows,
    current_step,
    file_if_present,
    is_app_job,
    list_jobs,
    prep_done,
    progress,
    read_state,
    saved_review,
    stage_status,
    stale_stages,
    tilde,
)

APP_KEYS = ("REPLICATE_API_TOKEN", "WAVESPEED_API_KEY")  # what a WaveSpeed swap needs (A4)
INTERRUPTED = "interrupted"  # what pipeline._tracked records on Ctrl-C
STOPPED = "Stopped before it finished."


@dataclass(frozen=True)
class Wipe:
    """One before/after player: the original on the left, a transformation on the right."""

    before: str
    after: str
    after_label: str
    sound: bool = False  # the after side carries sound (draft and final)


@dataclass(frozen=True)
class TabView:
    id: str
    title: str
    line: str
    tags: tuple[steps.Tag, ...]
    state: str  # "ready" | "running" | "waiting" | "failed" | "missing"
    url: str
    wipe: Wipe | None = None
    audio: tuple[tuple[str, str], ...] = ()  # (label, url)
    texts: tuple[tuple[str, str], ...] = ()  # (label, value)


@dataclass(frozen=True)
class ViewLink:
    """A tab on the draft or final screen: the result itself, or the swapped area."""

    id: str
    title: str
    url: str


@dataclass(frozen=True)
class BarItem:
    number: int
    title: str
    state: str  # "done" | "current" | "open" | "locked"
    url: str | None


@dataclass(frozen=True)
class MissingKey:
    name: str
    where: str


@dataclass(frozen=True)
class JobPage:
    id: str
    short_id: str
    step: steps.Step
    bar: tuple[BarItem, ...]
    mode: str
    read_only: bool
    progress: dict[str, Any]
    reachable: int  # the furthest step this job can show
    original: str | None = None  # the normalized clip: the "before" side of every wipe
    upload: str | None = None
    photo: str | None = None
    look: str = ""
    description: str = ""
    tabs: tuple[TabView, ...] = ()
    tab: TabView | None = None
    wipe: Wipe | None = None
    scene_line: str = ""  # a scene swap's draft and final: what was laid back (S14)
    views: tuple[ViewLink, ...] = ()  # a scene swap's draft and final: result | swapped area
    view: str = ""  # the selected view's id
    view_line: str = ""  # one line under the tabs for the swapped area
    state: str = "waiting"  # the step's own work: "ready" | "running" | "waiting" | "failed"
    error: str | None = None
    started_at: str = ""  # a running draft/final (for the elapsed timer)
    prep_done: bool = False
    approved: bool = False
    fingerprint: str = ""  # step 3 only: the review fingerprint, computed on this render
    stale: bool = False  # step 4: the draft was made from inputs no longer approved
    prompt: str = ""
    resolution: str = ""
    spent_usd: float = 0.0
    download: str | None = None
    tags: tuple[steps.Tag, ...] = ()  # the step's technique tags (none on a Wan draft/final)


@dataclass(frozen=True)
class HomePage:
    jobs: tuple[JobSummary, ...]
    all_jobs_usd: float
    missing: tuple[MissingKey, ...]


def missing_keys(settings: Settings) -> tuple[MissingKey, ...]:
    """The keys a WaveSpeed swap needs that .env does not set (names only, never values)."""
    return tuple(
        MissingKey(name, WHERE_TO_GET[name]) for name in APP_KEYS if not settings.get(name)
    )


def home_page(runs_dir: Path, settings: Settings) -> HomePage:
    return HomePage(tuple(list_jobs(runs_dir)), total_spend(runs_dir), missing_keys(settings))


def step_url(job_id: str, number: int) -> str:
    return f"/jobs/{job_id}/step/{number}"


def step_bar(job_id: str | None, current: int, reachable: int) -> tuple[BarItem, ...]:
    """1 Upload · 2 Prepare · 3 Check · 4 Draft · 5 Final; earlier steps can be revisited."""
    items = []
    for item in steps.STEPS:
        if item.number == current:
            state = "current"
        elif item.number < reachable:
            state = "done"
        elif item.number == reachable:
            state = "open"
        else:
            state = "locked"
        url = step_url(job_id, item.number) if job_id and state != "locked" else None
        items.append(BarItem(item.number, item.title, state, url))
    return tuple(items)


def _state_word(status: str) -> str:
    return {"done": "ready", "pending": "waiting"}.get(status, status)


def _error(state: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        entry = state["stages"].get(name)
        if isinstance(entry, dict) and entry.get("status") == "failed":
            error = str(entry.get("error") or steps.STEP_FAILED)
            return STOPPED if error == INTERRUPTED else tilde(error)
    return None


def _tab(job: Job, state: dict[str, Any], tab: steps.Tab, original: str | None) -> TabView:
    status = _state_word(combine([stage_status(state, name) for name in tab.stages]))
    head = (tab.id, tab.title, tab.line, tab.tags)
    url = f"{step_url(job.id, 2)}?tab={tab.id}"
    if status != "ready":
        return TabView(*head, status, url)
    if not tab.after:  # the voice: two audio players and the transcript
        audio = tuple(
            (label, found)
            for label, name in (
                ("Voice only", "vocals.wav"),
                ("Raised 3 semitones (what the AI hears)", "vocals-plus3.wav"),
            )
            if (found := file_if_present(job, name))
        )
        words = str(state.get("transcript") or "(no speech found)")
        return TabView(*head, "ready", url, audio=audio, texts=(("Words", words),))
    name = composite_name(job, state) if tab.after == "@composite" else tab.after
    after = file_if_present(job, name)
    if after is None or original is None:
        return TabView(*head, "missing", url)
    texts: tuple[tuple[str, str], ...] = ()
    if tab.id == "prepared" and stage_status(state, "describe") == "done":
        texts = (("Room", str(state.get("scene") or "(no description)")),)
    return TabView(*head, "ready", url, wipe=Wipe(original, after, tab.title), texts=texts)


def _pick_tab(tabs: tuple[TabView, ...], wanted: str, mode: str) -> TabView | None:
    """The requested tab if it is ready, else the ready one whose last stage runs latest
    in the pipeline (a prepared job opens on "What the AI gets"), else the first."""
    ready = [tab for tab in tabs if tab.state == "ready"]
    for tab in ready:
        if tab.id == wanted:
            return tab
    if ready:
        order = STAGES_BY_MODE[mode]
        last = {item.id: max(map(order.index, item.stages)) for item in steps.tabs_for(mode)}
        return max(ready, key=lambda tab: last[tab.id])
    return tabs[0] if tabs else None


def _step_tags(number: int, state: dict[str, Any]) -> tuple[steps.Tag, ...]:
    """The draft and final tags name Seedance, so a job made by another generator shows none."""
    if number >= 4 and state.get("generator") != "seedance":
        return ()
    return steps.step(number).tags


def _look(mode: str) -> str:
    for value, label, _hint in steps.LOOKS:
        if value == mode:
            return label
    return mode


def _prompt(job: Job, state: dict[str, Any]) -> str:
    """prompt.txt if it exists, else the default. Rendering never writes the file."""
    if state.get("generator") != "seedance":
        return ""
    path = job.path(PROMPT_FILE)
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ViewError(f"{PROMPT_FILE} could not be read: {exc}") from exc
    return default_prompt(job)


def _generation(job: Job, state: dict[str, Any], stage: str, original: str | None) -> dict:
    """The draft or final screen's player, status and error."""
    status = _state_word(stage_status(state, stage))
    entry = state["stages"].get(stage)
    entry = entry if isinstance(entry, dict) else {}
    found = {
        "state": status,
        "error": _error(state, (stage,)),
        "started_at": str(entry.get("started_at") or "") if status == "running" else "",
    }
    after = file_if_present(job, f"{stage}.mp4") if status == "ready" else None
    if after and original:
        label = "Draft" if stage == "draft" else "Final"
        found["wipe"] = Wipe(original, after, label, sound=True)
    elif status == "ready":
        found["state"] = "missing"
    return found


def job_page(job: Job, runs_dir: Path, number: int, *, tab: str = "") -> JobPage:
    """Everything one step screen shows. Raises ViewError for a damaged state.json."""
    state = read_state(job)
    key = generator_key(job, state)
    reachable = current_step(job, state)
    original = (
        file_if_present(job, "original.mp4") if stage_status(state, "normalize") == "done" else None
    )
    scene = file_if_present(job, "full-scene.mp4")  # a scene swap: the whole scene (S14)
    common: dict[str, Any] = {
        "id": job.id,
        "short_id": job.id[:8],
        "step": steps.step(number),
        "bar": step_bar(job.id, number, reachable),
        "mode": state["mode"],
        "read_only": not is_app_job(state, key),
        "progress": progress(state),
        "reachable": reachable,
        "original": original,
        "prep_done": prep_done(state),
        "spent_usd": completed_cost(cost_rows(job, state)),
        "tags": _step_tags(number, state),
    }
    decision = saved_review(job)
    if number == 1:
        return JobPage(
            **common,
            state="ready",
            upload=scene or file_if_present(job, "upload.mp4"),
            photo=file_if_present(job, state.get("character_primary")),
            look=_look(state["mode"]),
            description=str(state.get("character_description") or ""),
        )
    if number == 2:
        tabs = tuple(_tab(job, state, item, original) for item in steps.tabs_for(state["mode"]))
        names = tuple(common["progress"]["stages"])
        return JobPage(
            **common,
            tabs=tabs,
            tab=_pick_tab(tabs, tab, state["mode"]),
            state=_state_word(combine([stage_status(state, name) for name in names])),
            error=_error(state, names),
        )
    if number == 3:
        try:
            current = fingerprint(job)  # hashed on this page only (never by /api)
        except ReviewError:
            current = ""
        except OSError as exc:
            raise ViewError(f"The files to check could not be read: {exc}") from exc
        approved = bool(decision.get("approved")) and decision.get("fingerprint") == current
        prepared = file_if_present(job, "prepared.mp4") if common["prep_done"] else None
        wipe = Wipe(original, prepared, "What the AI gets") if original and prepared else None
        return JobPage(
            **common,
            wipe=wipe,
            state="ready" if wipe else "waiting",
            approved=approved,
            fingerprint=current,
        )
    stage = "draft" if number == 4 else "final"
    shown = _generation(job, state, stage, scene or original)
    final = state["stages"].get("final")
    resolution = final.get("resolution", "") if isinstance(final, dict) else ""
    wipe = shown.get("wipe")
    download = wipe.after if number == 5 and wipe else None
    swapped: dict[str, Any] = {}
    area = file_if_present(job, f"{stage}-area.mp4") if scene and wipe else None
    if scene and wipe and area:  # older scene jobs have no area file: no tab
        url = step_url(job.id, number)
        swapped["views"] = (
            ViewLink("result", wipe.after_label, url),
            ViewLink("area", steps.AREA, f"{url}?tab=area"),
        )
        swapped["view"] = "area" if tab == "area" else "result"
        if tab == "area":
            shown["wipe"] = Wipe(scene, area, steps.AREA)
            swapped["view_line"] = steps.AREA_LINE
    return JobPage(
        **common,
        approved=bool(decision.get("approved")),
        stale=number == 4 and "draft" in stale_stages(job, state),
        prompt=_prompt(job, state) if number == 4 else "",
        resolution=resolution if isinstance(resolution, str) else "",
        download=download,
        scene_line=steps.SCENE_LINE if scene else "",
        **shown,
        **swapped,
    )
