"""What the agent sees: a read-only copy of the database plus context.json, the dashboard's view of now."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import AppConfig
from .. import quarters
from ..grades import course_grades
from ..models import Brief, Course, Document, Item, Plan
from ..views import ItemOut, live_items, merged_items

DEADLINES = {"assignment", "quiz", "exam", "discussion"}
SCHEDULE = {"lecture", "section", "office_hours", "event", "exam", "quiz"}
DONE_STATUSES = {"submitted", "graded", "excused"}


def snapshot_db(src: Path, dest: Path) -> None:
    """A consistent copy of the live database, swapped in atomically.

    The copy is switched out of WAL mode: a WAL database can't be opened from a read-only folder
    (the reader needs to create its -shm file), and the agent mounts this one read-only.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    source, target = sqlite3.connect(src, timeout=15), sqlite3.connect(tmp)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        source.close()
    os.replace(tmp, dest)


def items_between(s: Session, configured: Sequence[str], start: datetime, end: datetime) -> list[ItemOut]:
    """Merged items dated in [start, end], plus undated ones, without the ones you've hidden."""
    q = live_items(configured).where(or_(Item.at.is_(None), Item.at.between(start, end)))
    return [i for i in merged_items(s, s.scalars(q).unique().all(), configured) if not i.hidden]


def is_open(i: ItemOut) -> bool:
    return i.kind in DEADLINES and not i.done and i.status not in DONE_STATUSES


def local(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return dt.astimezone(tz).isoformat(timespec="minutes") if dt else None


def item_dict(i: ItemOut, tz: ZoneInfo, briefs: set[int]) -> dict[str, Any]:
    fields = {
        "id": i.id, "course": i.course.code if i.course else None, "kind": i.kind, "title": i.title,
        "due": local(i.due_at, tz), "starts": local(i.starts_at, tz), "ends": local(i.ends_at, tz),
        "all_day": i.all_day, "location": i.location, "status": i.status, "points": i.points, "score": i.score,
        "category": i.category, "done": i.done, "pinned": i.pinned, "note": i.note, "url": i.url,
        "sources": [x.source for x in i.sources], "brief": i.id in briefs,
    }  # fmt: skip
    return {k: v for k, v in fields.items() if v not in (None, False, [], "")}


def latest_plans(s: Session, before: str | None = None, limit: int = 7) -> list[Plan]:
    q = select(Plan).where(Plan.status == "ready").order_by(Plan.date.desc(), Plan.id.desc())
    if before:
        q = q.where(Plan.date < before)
    out: dict[str, Plan] = {}
    for p in s.scalars(q.limit(limit * 4)):
        out.setdefault(p.date, p)  # the newest plan of each day is that day's plan
    return list(out.values())[:limit]


def ready_briefs(s: Session) -> dict[int, Brief]:
    return {b.item_id: b for b in s.scalars(select(Brief).where(Brief.status == "ready").order_by(Brief.id))}


def build_context(s: Session, configured: Sequence[str], config: AppConfig, now: datetime) -> dict[str, Any]:
    tz = ZoneInfo(config.timezone)
    items = items_between(s, configured, now - timedelta(days=14), now + timedelta(days=120))
    briefs = ready_briefs(s)
    courses = {c.id: c for c in s.scalars(select(Course))}
    graded = course_grades(
        merged_items(s, s.scalars(live_items(configured).where(Item.course_id.is_not(None))).unique().all(), configured),
        courses, config.courses,
    )  # fmt: skip
    live_courses = sorted({i.course.id for i in items if i.course})
    docs = Counter(s.execute(select(Document.course_id, Document.kind).where(Document.removed_at.is_(None))).all())
    library: dict[str, dict[str, int]] = {}
    for (course_id, kind), n in docs.items():
        library.setdefault(course_id or "general", {})[kind] = n
    return {
        "now": local(now, tz),
        "timezone": config.timezone,
        "quarter": quarters.current(quarters.course_terms(s, tz), now.astimezone(tz).date()),
        "planner": config.planner.model_dump(),
        "courses": [
            {"id": cid, "code": courses[cid].code, "name": courses[cid].name}
            | next(({"grade_estimate": g.estimate, "graded_share_of_course": g.counted_weight,
                     "categories": {c.name: c.percent for c in g.categories if c.percent is not None}}
                    for g in graded if g.course.id == cid), {})  # fmt: skip
            for cid in live_courses if cid in courses
        ],
        "items": [item_dict(i, tz, set(briefs)) for i in items],
        "plans": [
            {"date": p.date, "summary": p.summary, "note": p.note,
             "tasks": [{k: t.get(k) for k in ("title", "minutes", "item_id", "done")} for t in p.tasks]}
            for p in latest_plans(s)
        ],  # fmt: skip
        "briefs": [{"item_id": b.item_id, "title": b.title, "minutes": b.minutes, "written": local(b.finished_at, tz)}
                   for b in briefs.values()],  # fmt: skip
        "library": library,
    }


def write_snapshot(
    Session: Any, db_path: Path, out_dir: Path, configured: Sequence[str], config: AppConfig, now: datetime
) -> None:
    snapshot_db(db_path, out_dir / "dashboard.db")
    with Session() as s:
        ctx = build_context(s, configured, config, now)
    tmp = out_dir / "context.json.tmp"
    tmp.write_text(json.dumps(ctx, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, out_dir / "context.json")
