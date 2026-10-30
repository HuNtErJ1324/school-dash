"""App factory. Run with: uvicorn app.main:create_app --factory"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

from .agent.client import AgentClient
from .agent.jobs import Assistant
from .api import router
from .api_agent import router as agent_router
from .api_canvas import router as canvas_router
from .api_canvas import sessions as session_canvas_router
from .api_library import router as library_router
from .api_voice import router as voice_router
from .config import Settings, load_config
from .courses import sync_config_courses
from .db import make_engine, make_sessionmaker
from .dedupe import regroup
from .library import Library, LibrarySync, term_canvas_courses
from .migrate import upgrade
from .scheduler import Scheduler
from .sources import build_sources
from .sources.base import SourceContext
from .sync import run_source
from .voice import SpeechClient

USER_AGENT = "school-dashboard/0.1 (personal, self-hosted)"
mimetypes.add_type("font/woff2", ".woff2")  # the Canvas tab's fonts; slim images and Windows don't know it


class WebApp(StaticFiles):
    """The built UI. Page addresses it has no file for (/tutor, /tutor/12) get index.html, and the
    page routes itself; missing files and API paths stay 404s.

    /viz/ holds the libraries the agent's diagrams load. A diagram runs in a frame with no origin of its own,
    so its module scripts and fonts are cross-origin requests, which need the CORS header."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        parts = path.replace("\\", "/").strip("/").split("/")  # StaticFiles hands over an OS path
        try:
            response = await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or parts[0] == "api" or "." in parts[-1]:
                raise
            return await super().get_response("index.html", scope)
        if parts[0] == "viz":
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and feed URLs carry secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = load_config(settings.config_path)
        engine = make_engine(settings.data_dir / "dashboard.db")
        upgrade(engine)
        Session = make_sessionmaker(engine)
        sources, problems = build_sources(config.sources)
        live = [s.name for s in sources] + [p.name for p in problems]
        sync_config_courses(Session, config.courses, live)
        with Session.begin() as s:
            regroup(s)
        for p in problems:
            logging.getLogger(__name__).warning("source %s not started: %s", p.name, p.error)

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
            ctx = SourceContext(tz=ZoneInfo(config.timezone), http=http)
            scheduler = Scheduler(sources, lambda source: run_source(source, ctx, Session, live))
            canvas = next((s.config for s in sources if s.type == "canvas"), None)
            library = Library(settings.data_dir / "library", Session)
            library_sync = LibrarySync(library, config, http, Session, canvas, lambda: term_canvas_courses(Session, live))
            assistant = Assistant(
                AgentClient(settings.agent_url, config.agent, http), Session, config,
                db_path=settings.data_dir / "dashboard.db", snapshot_dir=settings.data_dir / "snapshot",
                configured=lambda: live,
            )  # fmt: skip
            app.state.config = config
            app.state.Session = Session
            app.state.scheduler = scheduler
            app.state.problems = problems
            app.state.library = library
            app.state.library_sync = library_sync
            app.state.assistant = assistant
            app.state.speech = SpeechClient(settings.speech_url, config.voice, http)
            app.state.background = set()
            app.state.uploads_dir = settings.data_dir / "uploads"
            app.state.canvases_dir = settings.data_dir / "canvases"
            if settings.sync_on_start:
                scheduler.start()
                library_sync.start()
                assistant.start()
            else:
                assistant.recover()
            try:
                yield
            finally:
                await assistant.stop()
                await library_sync.stop()
                await scheduler.stop()
        engine.dispose()

    app = FastAPI(title="School dashboard", lifespan=lifespan)
    app.include_router(router)
    app.include_router(agent_router)
    app.include_router(canvas_router)
    app.include_router(session_canvas_router)
    app.include_router(library_router)
    app.include_router(voice_router)
    if settings.static_dir and settings.static_dir.is_dir():
        app.mount("/", WebApp(directory=settings.static_dir, html=True), name="web")
    return app
