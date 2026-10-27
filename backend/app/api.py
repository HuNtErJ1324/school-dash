from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import quarters
from .dedupe import regroup, same_work
from .grades import CourseGrades, course_grades
from .models import Board, Course, Item, Overlay, SyncRun
from .views import CourseOut, ItemOut, live_items, merged_items

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.Session() as s:
        yield s


SessionDep = Annotated[Session, Depends(get_session)]


class OverlayPatch(BaseModel):
    """Only the fields you send change. An empty note clears it."""

    done: bool | None = None
    hidden: bool | None = None
    pinned: bool | None = None
    note: str | None = Field(None, max_length=2000)
    separate: bool | None = None  # stop merging this with look-alikes from other sources


class BoardIn(BaseModel):
    # Shapes are the frontend's business; the server just keeps them.
    widgets: list[dict[str, Any]] = Field(max_length=100)
    layouts: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Widget types the frontend has already added to this board once, so removing one sticks.
    offered: list[str] = Field(default_factory=list, max_length=100)


class BoardOut(BoardIn):
    name: str
    updated_at: datetime


BoardName = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    started_at: datetime
    finished_at: datetime
    ok: bool
    error: str | None
    items_seen: int


class SourceStatus(BaseModel):
    name: str
    type: str
    interval_minutes: float | None
    running: bool
    next_run_at: datetime | None
    last_run: RunOut | None
    last_success_at: datetime | None
    config_error: str | None = None


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


Quarter = Annotated[str | None, Query(pattern=r"^\d{2}(wi|sp|su|au)$", description='a quarter, "26au"')]


def _tz(request: Request) -> ZoneInfo:
    return ZoneInfo(request.app.state.config.timezone)


def _current(request: Request, terms: dict[str, str]) -> str:
    return quarters.current(terms, datetime.now(_tz(request)).date())


@router.get("/meta")
def meta(request: Request, s: SessionDep) -> dict[str, Any]:
    config = request.app.state.config
    current = _current(request, quarters.course_terms(s, _tz(request)))
    return {"quarter": current, "timezone": config.timezone, "now": datetime.now(timezone.utc)}


class QuarterOut(BaseModel):
    code: str  # "26au"
    name: str  # "Autumn 2026"
    courses: list[str]  # course ids


class QuartersOut(BaseModel):
    current: str  # the quarter the dashboard opens on
    quarters: list[QuarterOut]  # newest first


@router.get("/quarters", response_model=QuartersOut)
def list_quarters(request: Request, s: SessionDep) -> Any:
    terms = quarters.course_terms(s, _tz(request))
    current = _current(request, terms)
    codes = sorted({*terms.values(), current}, key=quarters.sort_key, reverse=True)
    return QuartersOut(current=current, quarters=[
        QuarterOut(code=c, name=quarters.name(c), courses=sorted(k for k, v in terms.items() if v == c)) for c in codes
    ])  # fmt: skip


def _configured_sources(request: Request) -> list[str]:
    state = request.app.state
    return [*state.scheduler.sources, *(p.name for p in state.problems)]


@router.get("/courses", response_model=list[CourseOut])
def list_courses(request: Request, s: SessionDep, active: bool = False, quarter: Quarter = None) -> Any:
    q = select(Course).order_by(Course.code)
    if active:  # only courses with items from sources in config.yaml right now
        live = select(Item.course_id).where(Item.removed_at.is_(None), Item.source.in_(_configured_sources(request)))
        q = q.where(Course.id.in_(live))
    terms = quarters.course_terms(s, _tz(request))
    rows = [c for c in s.scalars(q) if quarter is None or terms.get(c.id) == quarter]
    return [CourseOut.model_validate(c).model_copy(update={"term": terms.get(c.id)}) for c in rows]


@router.get("/items", response_model=list[ItemOut])
def list_items(
    request: Request,
    s: SessionDep,
    start: datetime | None = None,
    end: datetime | None = None,
    kind: Annotated[list[str] | None, Query()] = None,
    course: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
    status: Annotated[list[str] | None, Query(description='"none" matches items without a status')] = None,
    pinned: bool | None = None,
    include_hidden: bool = False,
    quarter: Quarter = None,  # that quarter's courses, and course-less items dated in it
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> Any:
    # Only sources in config.yaml right now: dropping a source hides its items, and adding
    # it back brings them back, without deleting anything.
    configured = _configured_sources(request)
    q = live_items(configured)
    if not include_hidden:
        q = q.where(Overlay.hidden.is_not(True))
    if pinned is not None:
        q = q.where(Overlay.pinned.is_(True) if pinned else Overlay.pinned.is_not(True))
    if start:
        q = q.where(Item.at >= _utc(start))
    if end:
        q = q.where(Item.at <= _utc(end))
    if kind:
        q = q.where(Item.kind.in_(kind))
    if course:
        q = q.where(Item.course_id.in_(course))
    if source:
        q = q.where(Item.source.in_(source))
    if quarter:
        ids = [c for c, t in quarters.course_terms(s, _tz(request)).items() if t == quarter]
        start, end = quarters.bounds(quarter, _tz(request))
        q = q.where(or_(Item.course_id.in_(ids), and_(Item.course_id.is_(None), Item.at >= start, Item.at < end)))
    q = q.order_by(Item.at.asc().nulls_last(), Item.id).limit(limit)

    items = merged_items(s, s.scalars(q).unique().all(), configured)
    if status:  # judged on the merged status: Gradescope's "submitted" beats a calendar's silence
        wanted = {None if v == "none" else v for v in status}
        items = [i for i in items if i.status in wanted]
    if not include_hidden:
        items = [i for i in items if not i.hidden]
    items.sort(key=lambda i: (i.at is None, i.at or datetime.min.replace(tzinfo=timezone.utc), i.id))
    return items


@router.patch("/items/{item_id}/overlay", response_model=ItemOut)
def patch_overlay(item_id: int, patch: OverlayPatch, request: Request, s: SessionDep) -> Any:
    item = s.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"no item {item_id}")
    now = datetime.now(timezone.utc)
    changes = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}  # null = leave it
    # The same work seen by several sources is one item to you: change every copy.
    group = item.group_id or item.id
    ids = s.scalars(select(Item.id).where(or_(Item.group_id == group, Item.id == item_id))).all()
    for member in ids:
        overlay = s.get(Overlay, member) or Overlay(item_id=member, done=False, hidden=False, pinned=False, separate=False)
        if "done" in changes and changes["done"] != overlay.done:
            overlay.done_at = now if changes["done"] else None
        for field, value in changes.items():
            setattr(overlay, field, (value.strip() or None) if field == "note" else value)
        overlay.updated_at = now
        s.add(overlay)
    if changes.get("separate") is False:
        # Undoing a split: the other copies were split off at the same time, so let them merge too.
        others = s.scalars(
            select(Overlay).join(Item, Item.id == Overlay.item_id)
            .where(Overlay.separate.is_(True), Item.course_id == item.course_id, Item.id.not_in(ids))
        ).all()  # fmt: skip
        for overlay in others:
            other = s.get(Item, overlay.item_id)
            if other.source_id == item.source_id or same_work(other.title, item.title):
                overlay.separate = False
                overlay.updated_at = now
    s.flush()
    if "separate" in changes:
        regroup(s)
    s.commit()
    s.expire_all()
    configured = _configured_sources(request)
    row = s.scalars(live_items([item.source]).where(Item.id == item_id)).unique().one()
    return merged_items(s, [row], configured)[0]


@router.get("/grades", response_model=list[CourseGrades])
def grades(request: Request, s: SessionDep, quarter: Quarter = None) -> Any:
    """Estimated grades for courses with graded work, weighted by config.yaml where it says how."""
    configured = _configured_sources(request)
    rows = s.scalars(live_items(configured).where(Item.course_id.is_not(None))).unique().all()
    items = merged_items(s, rows, configured)
    courses = {c.id: c for c in s.scalars(select(Course))}
    out = course_grades(items, courses, request.app.state.config.courses)
    if quarter:
        terms = quarters.course_terms(s, _tz(request))
        out = [g for g in out if terms.get(g.course.id) == quarter]
    return out


@router.get("/boards/{name}", response_model=BoardOut)
def get_board(name: BoardName, s: SessionDep) -> Any:
    board = s.get(Board, name)
    if board is None:
        raise HTTPException(404, f"no saved board named {name!r}")
    return BoardOut(name=board.name, updated_at=board.updated_at, **board.data)


@router.put("/boards/{name}", response_model=BoardOut)
def put_board(name: BoardName, body: BoardIn, s: SessionDep) -> Any:
    data = body.model_dump()
    board = s.get(Board, name) or Board(name=name)
    board.data = data
    board.updated_at = datetime.now(timezone.utc)
    s.add(board)
    s.commit()
    return BoardOut(name=name, updated_at=board.updated_at, **data)


def _statuses(request: Request, s: Session) -> list[SourceStatus]:
    scheduler = request.app.state.scheduler
    latest = {
        r.source: r
        for r in s.scalars(
            select(SyncRun).where(
                SyncRun.id.in_(select(func.max(SyncRun.id)).group_by(SyncRun.source))
            )
        )
    }
    last_ok = dict(s.execute(select(SyncRun.source, func.max(SyncRun.finished_at)).where(SyncRun.ok).group_by(SyncRun.source)).all())

    out = [
        SourceStatus(
            name=name,
            type=src.type,
            interval_minutes=src.interval.total_seconds() / 60,
            running=scheduler.is_running(name),
            next_run_at=scheduler.next_run_at.get(name),
            last_run=RunOut.model_validate(latest[name]) if name in latest else None,
            last_success_at=last_ok.get(name),
        )
        for name, src in scheduler.sources.items()
    ]
    out += [
        SourceStatus(
            name=p.name, type=p.type, interval_minutes=None, running=False, next_run_at=None,
            last_run=None, last_success_at=None, config_error=p.error,
        )  # fmt: skip
        for p in request.app.state.problems
    ]
    return out


@router.get("/sources", response_model=list[SourceStatus])
def list_sources(request: Request, s: SessionDep) -> Any:
    return _statuses(request, s)


@router.post("/sources/{name}/sync", response_model=SourceStatus)
async def sync_source(name: str, request: Request) -> Any:
    scheduler = request.app.state.scheduler
    if name not in scheduler.sources:
        raise HTTPException(404, f"no runnable source named {name!r}")
    await scheduler.run_now(name)
    with request.app.state.Session() as s:
        return next(st for st in _statuses(request, s) if st.name == name)


@router.post("/sync", response_model=list[SourceStatus])
async def sync_all(request: Request) -> Any:
    scheduler = request.app.state.scheduler
    await asyncio.gather(*(scheduler.run_now(name) for name in scheduler.sources))
    with request.app.state.Session() as s:
        return _statuses(request, s)
