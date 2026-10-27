"""Keeping the courses table in line with config.yaml, and matching sources' course refs to rows."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import CourseConfig
from .coursecode import course_slug, parse_course_code
from .models import Course, Item
from .quarters import course_terms, current, later
from .sources.base import CourseRef


# Colored pencils, for courses config.yaml doesn't color, in the order they're handed out.
# Checked with the dataviz palette validator on the light paper and, with the UI's dark
# steps, the chalkboard: every neighbouring pair stays apart for colorblind readers, and
# the first SAFE_TOGETHER stay apart in every pair, since all your courses show at once.
# Past those, the course code printed beside every color is what tells courses apart.
PENCILS = ["#2f6fd0", "#d9622b", "#0f9a75", "#b88200", "#6a4cd0", "#3b8a1e", "#c23f86", "#96592a"]
SAFE_TOGETHER = 3
# The first palette, which failed those checks; courses still wearing it get recolored.
_RETIRED = {"#2f62b5", "#a8327f", "#b07d00", "#12807d", "#6a4cb8", "#5f8a1c", "#c25a12", "#4f6379"}


def sync_config_courses(
    Session: sessionmaker[Session], courses: list[CourseConfig], live_sources: list[str] | None = None
) -> None:
    """config.yaml is the source of truth for the courses it lists."""
    with Session.begin() as s:
        for c in courses:
            row = s.get(Course, course_slug(c.code)) or Course(id=course_slug(c.code))
            row.code = c.code
            row.name = c.name or row.name
            row.color = c.color or row.color
            row.color_locked = bool(c.color)
            row.canvas_id = c.canvas_id if c.canvas_id is not None else row.canvas_id
            row.term = c.quarter or row.term
            row.links = {**(row.links or {}), **c.links}  # keep links sources found
            s.add(row)
        s.flush()
        assign_colors(s, live_sources)


def assign_colors(s: Session, live_sources: list[str] | None = None) -> None:
    """Give current courses distinct pencils, keeping each one's pencil when it can.

    Current means in the current quarter and having items from `live_sources` (every course,
    if None). Other quarters' courses don't hold pencils back, and a course on a later pencil
    moves to one of the first SAFE_TOGETHER when one is free. Colors set in config.yaml never
    change.
    """
    rows = s.scalars(select(Course).order_by(Course.code)).all()
    for row in rows:
        if row.color in _RETIRED and not row.color_locked:
            row.color = None
    if live_sources is None:
        live = {r.id for r in rows}
    else:
        live = set(s.scalars(
            select(Item.course_id).where(Item.removed_at.is_(None), Item.source.in_(live_sources), Item.course_id.is_not(None))
        ))  # fmt: skip
    # Courses from another quarter keep their items (and their grades), but not a pencil.
    terms = course_terms(s, UTC)
    now = current(terms, datetime.now(UTC).date())
    live = {c for c in live if terms.get(c, now) == now}

    taken: dict[str, str] = {}  # color -> course holding it; locked colors claim theirs first
    for row in sorted(rows, key=lambda r: (not r.color_locked, r.code)):
        if row.color and (row.color_locked or row.id in live) and row.color not in taken:
            taken[row.color] = row.id

    safe = PENCILS[:SAFE_TOGETHER]
    for row in rows:
        if row.id not in live or row.color_locked:
            continue
        holds = row.color is not None and taken.get(row.color) == row.id
        if holds and (row.color in safe or all(p in taken for p in safe)):
            continue  # keep it
        if holds:
            del taken[row.color]
        free = [p for p in PENCILS if p not in taken]
        row.color = free[0] if free else PENCILS[len(taken) % len(PENCILS)]
        taken[row.color] = row.id


class CourseResolver:
    """Maps a source's CourseRef to a course id, creating courses that config.yaml doesn't list."""

    def __init__(self, session: Session):
        self.s = session
        self._cache: dict[tuple[int | None, str | None, str | None], str | None] = {}

    def resolve(self, ref: CourseRef) -> str | None:
        key = (ref.canvas_id, ref.code, ref.name)
        if key not in self._cache:
            self._cache[key] = self._resolve(ref)
        return self._cache[key]

    def _resolve(self, ref: CourseRef) -> str | None:
        code = ref.code or parse_course_code(ref.name)
        course = None
        if ref.canvas_id is not None:
            course = self.s.scalar(select(Course).where(Course.canvas_id == ref.canvas_id))
        if course is None and code:
            course = self.s.get(Course, course_slug(code))

        if course is None:
            if not code and ref.canvas_id is None:
                return None
            course = Course(
                id=course_slug(code) if code else f"canvas{ref.canvas_id}",
                code=code or ref.name or f"Canvas {ref.canvas_id}",
                name=ref.name,
                canvas_id=ref.canvas_id,
                links=dict(ref.links),
                term=ref.term,
            )
            self.s.add(course)
            self.s.flush()
        else:
            if ref.canvas_id is not None and course.canvas_id is None:
                course.canvas_id = ref.canvas_id
            if ref.name and not course.name:
                course.name = ref.name
            if ref.term:
                course.term = later(course.term, ref.term)  # taken again later: the newer quarter
            if any(k not in (course.links or {}) for k in ref.links):
                course.links = {**ref.links, **(course.links or {})}  # existing links (config's) win
        return course.id
