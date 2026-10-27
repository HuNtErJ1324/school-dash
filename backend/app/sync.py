"""Runs a source and stores what it found: upsert items, mark vanished ones removed, log the run."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import or_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from .courses import CourseResolver, assign_colors
from .dedupe import regroup
from .models import Item, SyncRun
from .sources.base import FetchResult, Source, SourceContext, SourceError

log = logging.getLogger(__name__)


async def run_source(
    source: Source, ctx: SourceContext, Session: sessionmaker[Session], live_sources: list[str] | None = None
) -> SyncRun:
    started = ctx.now()
    seen, error = 0, None
    try:
        result = await source.fetch(ctx)
        seen = await asyncio.to_thread(store_result, Session, source.name, result, started, live_sources)
    except SourceError as e:
        error = str(e)
    except Exception as e:  # a broken adapter shouldn't take the scheduler down with it
        log.exception("source %s failed", source.name)
        error = f"{type(e).__name__}: {e}"

    run = SyncRun(
        source=source.name, started_at=started, finished_at=ctx.now(),
        ok=error is None, error=error, items_seen=seen,
    )  # fmt: skip
    await asyncio.to_thread(_save, Session, run)
    secs = (run.finished_at - started).total_seconds()
    if error:
        log.warning("sync %s failed after %.1fs: %s", source.name, secs, error)
    else:
        log.info("sync %s: %d items in %.1fs", source.name, seen, secs)
    return run


def store_result(
    Session: sessionmaker[Session],
    source_name: str,
    result: FetchResult,
    now: datetime,
    live_sources: list[str] | None = None,  # the sources in config.yaml, for course colors
) -> int:
    with Session.begin() as s:
        courses = CourseResolver(s)
        for ref in result.courses:
            courses.resolve(ref)

        for item in result.items:
            values = dict(
                source=source_name,
                source_id=item.source_id,
                course_id=courses.resolve(item.course) if item.course else None,
                kind=item.kind,
                title=item.title,
                url=item.url,
                due_at=item.due_at,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                at=item.due_at or item.starts_at,
                all_day=item.all_day,
                location=item.location,
                status=item.status,
                points=item.points,
                score=item.score,
                category=item.category,
                raw=item.raw,
                last_seen_at=now,
                removed_at=None,
            )
            stmt = sqlite_insert(Item).values(first_seen_at=now, **values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Item.source, Item.source_id],
                set_={k: stmt.excluded[k] for k in values},
            )
            s.execute(stmt)

        # Anything this source stored before but didn't return now is gone (deleted, renamed,
        # moved) — but only judge items inside the window the fetch actually looked at.
        gone = update(Item).where(
            Item.source == source_name,
            Item.removed_at.is_(None),
            Item.last_seen_at < now,
        )
        if result.window:
            start, end = result.window
            gone = gone.where(or_(Item.at.is_(None), Item.at.between(start, end)))
        s.execute(gone.values(removed_at=now))
        assign_colors(s, live_sources)  # for courses this sync created
        regroup(s)  # this source's items may match other sources' now
    return len(result.items)


def _save(Session: sessionmaker[Session], run: SyncRun) -> None:
    with Session.begin() as s:
        s.add(run)
