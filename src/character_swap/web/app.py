"""The click-through app (FastAPI + Jinja2). `charswap app` binds it to 127.0.0.1."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from character_swap.config import Settings
from character_swap.costs import PRICES, estimate_prepare, usd_text
from character_swap.jobs import Job, JobError
from character_swap.web import steps
from character_swap.web.pages import home_page, job_page, missing_keys, step_bar, step_url
from character_swap.web.view_model import (
    ESTIMATE_SECONDS,
    ViewError,
    current_step,
    progress,
    read_state,
    tilde,
)

HERE = Path(__file__).parent
# JSON (state, prediction records, landmarks) is read server-side and never served.
ALLOWED_SUFFIXES = {".mp4", ".wav", ".jpg", ".jpeg", ".png", ".webp"}
# Only answer requests addressed to this machine (blocks DNS-rebinding pages from reading jobs).
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
NOT_FOUND = "Not found."
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    ),
}
# WaveSpeed bills the input seconds plus the output seconds: the draft hint on step 1.
DRAFT_PER_SECOND = PRICES["wavespeed-480"].usd + PRICES["wavespeed-480-input"].usd


def create_app(
    settings: Settings, *, reload_settings: Callable[[], Settings] | None = None
) -> FastAPI:
    """`reload_settings` re-reads .env on each request (A4); tests pass fixed settings."""
    runs_dir = settings.runs_dir
    current_settings = reload_settings or (lambda: settings)
    app = FastAPI(title="Character Swap", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def harden(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"  # pages show private job data
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(steps=steps, money=usd_text)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
        context = {"title": title, "message": tilde(text)}
        return templates.TemplateResponse(request, "message.html", context, status_code=status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
        title = "Not found" if exc.status_code == 404 else "Not allowed"
        response = message(request, title, str(exc.detail), exc.status_code)
        response.headers.update(exc.headers or {})
        return response

    def load(job_id: str) -> Job:
        try:
            return Job.load(runs_dir, job_id)
        except JobError:
            raise HTTPException(404, "There is no swap with that id.") from None

    def damaged(request: Request, job_id: str, exc: Exception) -> Response:
        return message(request, f"Swap {job_id[:8]} cannot be shown", str(exc), 500)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        page = home_page(runs_dir, current_settings())
        return templates.TemplateResponse(request, "home.html", {"page": page})

    @app.get("/new", response_class=HTMLResponse)
    def new_swap(request: Request) -> Response:
        context = {
            "step": steps.step(1),
            "bar": step_bar(None, 1, 1),
            "missing": missing_keys(current_settings()),
            "prepare_usd": estimate_prepare("depth_mesh", seconds=ESTIMATE_SECONDS).total,
            "draft_per_second": f"{DRAFT_PER_SECOND:.4f}",
        }
        return templates.TemplateResponse(request, "new.html", context)

    @app.get("/jobs/{job_id}")
    def job_home(request: Request, job_id: str) -> Response:
        job = load(job_id)
        try:
            number = current_step(job, read_state(job))
        except ViewError as exc:
            return damaged(request, job_id, exc)
        return RedirectResponse(step_url(job.id, number), status_code=303)

    @app.get("/jobs/{job_id}/step/{number}", response_class=HTMLResponse)
    def step_page(request: Request, job_id: str, number: int, tab: str = "") -> Response:
        job = load(job_id)
        if not 1 <= number <= 5:
            raise HTTPException(404, NOT_FOUND)
        try:
            reachable = current_step(job, read_state(job))
            if number > reachable:  # a later step is not open yet
                return RedirectResponse(step_url(job.id, reachable), status_code=303)
            page = job_page(job, runs_dir, number, tab=tab)
        except ViewError as exc:
            return damaged(request, job_id, exc)
        shown = {"stages": page.progress["stages"], "generation": page.progress["generation"]}
        context = {"page": page, "bar": page.bar, "progress_json": json.dumps(shown)}
        return templates.TemplateResponse(request, "job.html", context)

    @app.get("/jobs/{job_id}/files/{name:path}")
    def job_file(job_id: str, name: str) -> FileResponse:
        job = load(job_id)
        if "\x00" in name or Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise HTTPException(404, NOT_FOUND)
        try:
            path = job.path(name)  # containment: nothing outside the job folder
        except (JobError, ValueError):
            raise HTTPException(404, NOT_FOUND) from None
        if not path.is_file():
            raise HTTPException(404, NOT_FOUND)
        # Range requests (video seeking) are handled by FileResponse.
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs/{job_id}")
    def job_progress(job_id: str) -> JSONResponse:
        job = load(job_id)
        try:
            state = read_state(job)
        except ViewError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(progress(state), headers={"Cache-Control": "no-store"})

    return app
