"""Items as the API shows them: one entry per piece of work, merged across the sources that see it."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from .models import Item

# The most informative status any source reports wins: if Gradescope says graded and the
# course calendar knows nothing, it's graded; if one source says submitted and another
# still says missing, it's submitted.
STATUS_RANK = {"graded": 5, "excused": 4, "submitted": 3, "missing": 2, "pending": 1, None: 0}


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str | None
    color: str | None
    links: dict[str, str]
    term: str | None = None  # the quarter, "26au"


class SourceLink(BaseModel):
    source: str
    url: str | None


class ItemOut(BaseModel):
    id: int  # the group's id; overlay changes made through it apply to every source's copy
    source: str  # the source the main fields come from
    source_id: str
    course: CourseOut | None
    kind: str
    title: str
    url: str | None
    at: datetime | None
    due_at: datetime | None
    starts_at: datetime | None
    ends_at: datetime | None
    all_day: bool
    location: str | None
    status: str | None
    points: float | None
    score: float | None
    category: str | None
    sources: list[SourceLink]  # every source that has this item, with its link
    # Your overlay. `done` is what you marked; `status` is what the sources say.
    done: bool
    hidden: bool
    pinned: bool
    note: str | None
    separate: bool


def live_items(configured: Sequence[str]) -> Select[tuple[Item]]:
    """Items from sources in config.yaml, with course and overlay loaded."""
    return (
        select(Item)
        .outerjoin(Item.overlay)
        .options(joinedload(Item.course), contains_eager(Item.overlay))
        .where(Item.removed_at.is_(None), Item.source.in_(configured))
    )


def merge(members: Sequence[Item]) -> ItemOut:
    members = sorted(members, key=lambda i: i.id)
    # Main fields from a copy with a real date and time, preferring one that knows its status.
    main = min(members, key=lambda i: (i.at is None, i.all_day, i.status is None, i.id))
    scored = next((m for m in members if m.score is not None), None)
    first = lambda attr: next((getattr(m, attr) for m in members if getattr(m, attr)), None)  # noqa: E731
    return ItemOut(
        id=members[0].id,
        source=main.source,
        source_id=main.source_id,
        course=CourseOut.model_validate(main.course) if main.course else None,
        kind=main.kind,
        title=main.title,
        url=main.url,
        at=main.at,
        due_at=main.due_at,
        starts_at=main.starts_at,
        ends_at=main.ends_at,
        all_day=main.all_day,
        location=main.location or first("location"),
        status=max((m.status for m in members), key=lambda st: STATUS_RANK.get(st, 0)),
        points=scored.points if scored else main.points or first("points"),
        score=scored.score if scored else None,
        category=first("category"),
        sources=[SourceLink(source=m.source, url=m.url) for m in members],
        done=any(m.done for m in members),
        hidden=any(m.hidden for m in members),
        pinned=any(m.pinned for m in members),
        note=first("note"),
        separate=any(m.overlay is not None and m.overlay.separate for m in members),
    )


def merged_items(s: Session, rows: Sequence[Item], configured: Sequence[str]) -> list[ItemOut]:
    """Expand matched rows to their whole groups and merge each group, keeping row order."""
    order = list(dict.fromkeys(r.group_id or r.id for r in rows))
    groups: dict[int, list[Item]] = defaultdict(list)
    for m in s.scalars(live_items(configured).where(Item.group_id.in_(order))).unique():
        groups[m.group_id].append(m)
    for r in rows:  # not grouped yet (between a sync and its regroup)
        if r.group_id is None:
            groups[r.id] = [r]
    return [merge(groups[g]) for g in order if groups[g]]
