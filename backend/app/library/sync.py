"""Keeps the library current: reads each course site and Canvas on an interval and on demand."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import AppConfig, SiteConfig
from ..coursecode import course_slug, parse_course_code
from ..models import Course, Item, SyncRun
from ..sources.base import SourceError
from ..sources.canvas import CanvasApi, CanvasConfig
from .canvas_docs import fetch_canvas
from .site import crawl_site
from .store import Fetched, Library

log = logging.getLogger(__name__)


@dataclass
class LibraryRun:
    name: str  # "site:cse410", "canvas"
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    saved: int = 0  # new or changed documents
    unchanged: int = 0
    removed: int = 0
    skipped: list[str] = field(default_factory=list)  # files it couldn't read, and why


class LibrarySync:
    def __init__(
        self,
        library: Library,
        config: AppConfig,
        http: httpx.AsyncClient,
        Session: sessionmaker[Session],
        canvas: CanvasConfig | None,
        canvas_courses: Callable[[], list[tuple[str, int]]],  # the term's courses: (course id, Canvas id)
    ):
        self.library = library
        self.config = config
        self.http = http
        self.Session = Session
        self.canvas = canvas if config.library.canvas else None
        self.canvas_courses = canvas_courses
        self.tz = ZoneInfo(config.timezone)
        self.runs: dict[str, LibraryRun] = {}
        self.next_run_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    @property
    def sites(self) -> list[SiteConfig]:
        """`library.sites`, plus the `links.site` of every course that isn't listed there."""
        sites = list(self.config.library.sites)
        listed = {_slug(s.course) for s in sites}
        for c in self.config.courses:
            if c.links.get("site") and _slug(c.code) not in listed:
                sites.append(SiteConfig(course=c.code, url=c.links["site"]))
        return sites

    @property
    def fetchers(self) -> list[str]:
        return [f"site:{_slug(s.course)}" for s in self.sites] + (["canvas"] if self.canvas else [])

    def start(self, delay: float = 30) -> None:
        self._task = asyncio.create_task(self._loop(delay), name="library")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def run_now(self) -> list[LibraryRun]:
        if self._lock.locked():  # already reading: wait for it rather than starting another
            async with self._lock:
                return list(self.runs.values())
        async with self._lock:
            max_bytes = int(self.config.library.max_file_mb * 2**20)
            for site in self.sites:
                course_id = _slug(site.course)
                known = await asyncio.to_thread(self.library.known_versions, "site")
                await self._run(f"site:{course_id}", "site", [course_id],
                                lambda problems: crawl_site(self.http, site, course_id, known, max_bytes, problems))  # fmt: skip
            if self.canvas:
                api, courses = CanvasApi(self.canvas, self.http), self.canvas_courses()
                known = await asyncio.to_thread(self.library.known_versions, "canvas")
                await self._run("canvas", "canvas", [c for c, _ in courses],
                                lambda problems: fetch_canvas(api, courses, known, max_bytes, problems))  # fmt: skip
            await asyncio.to_thread(self.library.write_index, self.tz)
            return list(self.runs.values())

    async def _run(
        self, name: str, source: str, course_ids: list[str], fetch: Callable[[list[str]], AsyncIterator[Fetched]]
    ) -> None:
        run = LibraryRun(name=name, started_at=datetime.now(timezone.utc))
        self.runs[name] = run
        seen: set[str] = set()
        try:
            async for f in fetch(run.skipped):
                await asyncio.to_thread(self.library.save, f)
                seen.add(f.key)
                if f.unchanged:
                    run.unchanged += 1
                else:
                    run.saved += 1
            # Only a complete read can say what's gone, and only for the courses it read.
            run.removed = await asyncio.to_thread(self.library.mark_removed, source, seen, course_ids, datetime.now(timezone.utc))
        except SourceError as e:
            run.error = str(e)
        except Exception as e:
            log.exception("library %s failed", name)
            run.error = f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)
        with self.Session.begin() as s:
            s.add(SyncRun(source=f"library:{name}", started_at=run.started_at, finished_at=run.finished_at,
                          ok=run.error is None, error=run.error, items_seen=run.saved + run.unchanged))  # fmt: skip
        log.info("library %s: %d new or changed, %d unchanged, %d gone, %d skipped%s", name, run.saved,
                 run.unchanged, run.removed, len(run.skipped), f"; failed: {run.error}" if run.error else "")  # fmt: skip

    async def _loop(self, delay: float) -> None:
        await asyncio.sleep(delay)
        interval = timedelta(hours=self.config.library.interval_hours)
        while True:
            try:
                await self.run_now()
            except Exception:
                log.exception("library sync crashed")
            self.next_run_at = datetime.now(timezone.utc) + interval
            await asyncio.sleep(interval.total_seconds())


def _slug(course: str) -> str:
    return course_slug(parse_course_code(course) or course)


def term_canvas_courses(Session: sessionmaker[Session], live_sources: list[str]) -> list[tuple[str, int]]:
    """Courses with a Canvas id that this term's sources have items for."""
    with Session() as s:
        live = select(Item.course_id).where(Item.removed_at.is_(None), Item.source.in_(live_sources))
        rows = s.execute(select(Course.id, Course.canvas_id).where(Course.canvas_id.is_not(None), Course.id.in_(live)))
        return [(cid, canvas_id) for cid, canvas_id in rows]
