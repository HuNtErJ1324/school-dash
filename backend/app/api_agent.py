"""The agent's API: the day's plan, assignment briefs, conversations, and photos for them. Jobs run in
the background; their rows say `running` until they're done, so pages poll."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .agent.jobs import Assistant
from .agent.schemas import BriefOut, ConversationOut, PlanOut
from .api import SessionDep
from .canvases import remove_all as remove_canvas_files
from .models import Brief, Canvas, Conversation, Course, Plan
from .uploads import MAX_BYTES, PhotoError, photo_path, save_photo

router = APIRouter(prefix="/api")


def _assistant(request: Request) -> Assistant:
    return request.app.state.assistant


class AgentStatus(BaseModel):
    enabled: bool  # an agent container is configured
    ready: bool  # it answers, with prime-agent installed and an API key
    problem: str | None  # what to fix when it isn't ready
    model: str
    busy: bool
    next_plan_at: datetime | None
    spent_this_month: float  # dollars, as prime-agent reports them


@router.get("/agent", response_model=AgentStatus)
async def agent_status(request: Request) -> Any:
    return await _assistant(request).status()


def _require_agent(request: Request) -> Assistant:
    assistant = _assistant(request)
    if not assistant.client.enabled:
        raise HTTPException(503, "There's no agent container: AGENT_URL isn't set.")
    return assistant


class TodayPlan(BaseModel):
    plan: PlanOut | None  # the newest finished plan for today
    pending: PlanOut | None  # a newer attempt that's still running or failed


@router.get("/plans/today", response_model=TodayPlan)
def today_plan(request: Request, s: SessionDep) -> Any:
    date = _assistant(request).today()
    rows = s.scalars(select(Plan).where(Plan.date == date).order_by(Plan.id.desc())).all()
    ready = next((p for p in rows if p.status == "ready"), None)
    newest = rows[0] if rows else None
    return TodayPlan(plan=ready, pending=newest if newest is not None and newest is not ready else None)


class PlanRequest(BaseModel):
    note: str | None = Field(None, max_length=1000)  # "I only have two hours", "focus on the midterm"


@router.post("/plans", response_model=PlanOut, status_code=202)
def make_plan(body: PlanRequest, request: Request, s: SessionDep) -> Any:
    plan_id = _require_agent(request).start_plan((body.note or "").strip() or None)
    return s.get(Plan, plan_id)


class TaskPatch(BaseModel):
    done: bool


@router.patch("/plans/{plan_id}/tasks/{index}", response_model=PlanOut)
def patch_task(plan_id: int, index: int, body: TaskPatch, request: Request) -> Any:
    plan = _assistant(request).set_task_done(plan_id, index, body.done)
    if plan is None:
        raise HTTPException(404, f"no task {index} in plan {plan_id}")
    return plan


class ItemBrief(BaseModel):
    brief: BriefOut | None  # the newest finished brief
    pending: BriefOut | None  # a newer attempt that's still running or failed


@router.get("/briefs", response_model=list[BriefOut])
def list_briefs(s: SessionDep) -> Any:
    """The newest finished brief for every item that has one."""
    latest: dict[int, Brief] = {}
    for b in s.scalars(select(Brief).where(Brief.status == "ready").order_by(Brief.id)):
        latest[b.item_id] = b
    return list(latest.values())


@router.get("/items/{item_id}/brief", response_model=ItemBrief)
def item_brief(item_id: int, request: Request, s: SessionDep) -> Any:
    item = _assistant(request).item(s, item_id)
    if item is None:
        raise HTTPException(404, f"no item {item_id}")
    rows = s.scalars(select(Brief).where(Brief.item_id == item.id).order_by(Brief.id.desc())).all()
    ready = next((b for b in rows if b.status == "ready"), None)
    newest = rows[0] if rows else None
    return ItemBrief(brief=ready, pending=newest if newest is not None and newest is not ready else None)


@router.post("/items/{item_id}/brief", response_model=BriefOut, status_code=202)
def write_brief(item_id: int, request: Request, s: SessionDep) -> Any:
    try:
        brief_id = _require_agent(request).start_brief(item_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return s.get(Brief, brief_id)


class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    item_id: int | None = None  # asked from an item's menu, or the exam a review session is for
    photos: list[str] = Field(default_factory=list, max_length=4)  # ids from POST /api/uploads


def _photos(request: Request, ids: list[str]) -> list[str]:
    folder = request.app.state.uploads_dir
    if any(photo_path(folder, p) is None for p in ids):
        raise HTTPException(422, "a photo with that id hasn't been uploaded")
    return list(dict.fromkeys(ids))


class PhotoOut(BaseModel):
    id: str
    width: int
    height: int
    size: int


@router.post("/uploads", response_model=PhotoOut, status_code=201)
async def upload_photo(request: Request, file: Annotated[UploadFile, File()]) -> Any:
    """A photo for a tutoring session: your work on a practice problem, a diagram."""
    content = await file.read(MAX_BYTES + 1)
    try:
        return (await asyncio.to_thread(save_photo, content, request.app.state.uploads_dir)).__dict__
    except PhotoError as e:
        raise HTTPException(413 if len(content) > MAX_BYTES else 422, str(e)) from e


@router.get("/uploads/{photo_id}")
def get_photo(photo_id: str, request: Request) -> FileResponse:
    path = photo_path(request.app.state.uploads_dir, photo_id)
    if path is None:
        raise HTTPException(404, "no such photo")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})


class StartConversation(Ask):
    course_id: str | None = None
    mode: Literal["ask", "explain", "quiz", "review"] = "ask"  # tutoring modes (agent/prompts.py)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(s: SessionDep, limit: int = 50) -> Any:
    rows = s.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)).all()
    return [ConversationOut(id=c.id, title=c.title, item_id=c.item_id, course_id=c.course_id, mode=c.mode,
                            canvas_id=c.canvas_id, created_at=c.created_at, updated_at=c.updated_at)
            for c in rows]  # fmt: skip


def _conversation(s: SessionDep, conv_id: int) -> Conversation:
    conv = s.scalars(
        select(Conversation).options(selectinload(Conversation.messages)).where(Conversation.id == conv_id)
    ).one_or_none()  # fmt: skip
    if conv is None:
        raise HTTPException(404, f"no conversation {conv_id}")
    return conv


@router.get("/conversations/{conv_id}", response_model=ConversationOut)
def get_conversation(conv_id: int, s: SessionDep) -> Any:
    return _conversation(s, conv_id)


@router.post("/conversations", response_model=ConversationOut, status_code=202)
def start_conversation(body: StartConversation, request: Request, s: SessionDep) -> Any:
    if body.course_id is not None and s.get(Course, body.course_id) is None:
        raise HTTPException(422, f"no course {body.course_id!r}")
    photos = _photos(request, body.photos)
    conv_id = _require_agent(request).start_conversation(body.text.strip(), body.item_id, body.course_id, body.mode, photos)
    return _conversation(s, conv_id)


@router.post("/conversations/{conv_id}/messages", response_model=ConversationOut, status_code=202)
def send_message(conv_id: int, body: Ask, request: Request, s: SessionDep) -> Any:
    photos = _photos(request, body.photos)
    try:
        _require_agent(request).add_message(conv_id, body.text.strip(), photos)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return _conversation(s, conv_id)


@router.delete("/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: int, request: Request) -> None:
    """Deletes a session, and its canvas if it opened one."""
    with request.app.state.Session.begin() as s:
        conv = _conversation(s, conv_id)
        canvas = s.scalars(select(Canvas).where(Canvas.conversation_id == conv_id)).first()
        if canvas is not None:
            s.delete(canvas)
        s.delete(conv)
    if canvas is not None:
        await asyncio.to_thread(remove_canvas_files, request.app.state.canvases_dir, canvas.id)
    await _assistant(request).client.reset(f"chat:{conv_id}")
