"""Read-only local job viewer. GET routes only; `charswap view` binds it to 127.0.0.1."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from character_swap.config import Settings
from character_swap.costs import total_spend
from character_swap.jobs import Job, JobError
from character_swap.web.view_model import (
    ICONS,
    STATUS_WORDS,
    ViewError,
    job_view,
    list_jobs,
    progress,
    read_state,
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


def create_app(settings: Settings) -> FastAPI:
    runs_dir = settings.runs_dir
    app = FastAPI(title="Character Swap viewer", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def harden(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"  # pages show private job data
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(icons=ICONS, status_words=STATUS_WORDS)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def message(request: Request, title: str, text: str, status_code: int) -> Response:
        context = {"title": title, "message": text}
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
            raise HTTPException(404, "There is no job with that id.") from None

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        context = {"jobs": list_jobs(runs_dir), "all_jobs_usd": total_spend(runs_dir)}
        return templates.TemplateResponse(request, "jobs.html", context)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> Response:
        job = load(job_id)
        try:
            view = job_view(job, runs_dir)
        except ViewError as exc:
            return message(request, f"Job {job_id[:8]} cannot be shown", str(exc), 500)
        context = {"job": view, "stages_json": json.dumps(view.progress["stages"])}
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
