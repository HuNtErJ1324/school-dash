"""Whiteboards shared with the tutor: the Canvas tab's, each with its own conversation, and the one a Tutor
session opens beside its chat. The page owns the drawing (an Excalidraw scene, saved as you go); asking the
tutor sends a snapshot of it with the message, and the tutor's reply says what to add to it (agent/runner.py,
"The shared canvas")."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .agent.prompts import HELP_TEXT
from .agent.schemas import ConversationOut
from .api import SessionDep
from .api_agent import _assistant, _conversation, _photos, _require_agent
from .canvases import MAX_FILE_BYTES, CanvasFileError, file_path, list_files, remove_all, save_file
from .models import Canvas, Conversation, Course

router = APIRouter(prefix="/api/canvases")
sessions = APIRouter(prefix="/api/conversations")


class CanvasSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    course_id: str | None
    conversation_id: int | None
    created_at: datetime
    updated_at: datetime


class CanvasFileOut(BaseModel):
    id: str
    mime_type: str


class CanvasOut(CanvasSummary):
    scene: dict[str, Any]
    state: dict[str, Any]
    files: list[CanvasFileOut]  # fetch each from /api/canvases/<id>/files/<file id>


def _root(request: Request):
    return request.app.state.canvases_dir


def _canvas(s: SessionDep, canvas_id: int) -> Canvas:
    canvas = s.get(Canvas, canvas_id)
    if canvas is None:
        raise HTTPException(404, f"no canvas {canvas_id}")
    return canvas


def _out(request: Request, canvas: Canvas) -> CanvasOut:
    files = [CanvasFileOut(id=f.id, mime_type=f.mime_type) for f in list_files(_root(request), canvas.id)]
    return CanvasOut(**CanvasSummary.model_validate(canvas).model_dump(), scene=canvas.scene or {},
                     state=canvas.state or {}, files=files)  # fmt: skip


@router.get("", response_model=list[CanvasSummary])
def list_canvases(s: SessionDep, limit: int = 100) -> Any:
    """The Canvas tab's canvases. A Tutor session's canvas opens from its session."""
    own = select(Canvas).outerjoin(Conversation, Canvas.conversation_id == Conversation.id)
    own = own.where((Canvas.conversation_id.is_(None)) | (Conversation.mode == "canvas"))
    return s.scalars(own.order_by(Canvas.updated_at.desc()).limit(limit)).all()


@sessions.post("/{conv_id}/canvas", response_model=CanvasOut)
def session_canvas(conv_id: int, request: Request, s: SessionDep) -> Any:
    """A Tutor session's canvas, made the first time it's opened. The replies already in the session count as
    drawn, so opening it doesn't replay them."""
    conv = _conversation(s, conv_id)
    canvas = s.scalars(select(Canvas).where(Canvas.conversation_id == conv_id)).first()
    if canvas is None:
        now = _assistant(request).now()
        replies = [m.id for m in conv.messages if m.role == "assistant"]
        canvas = Canvas(title=conv.title, course_id=conv.course_id, conversation_id=conv_id, scene={},
                        state={"applied": replies}, created_at=now, updated_at=now)  # fmt: skip
        s.add(canvas)
        s.commit()
    return _out(request, canvas)


class NewCanvas(BaseModel):
    title: str = Field("Untitled problem", min_length=1, max_length=200)
    course_id: str | None = None


@router.post("", response_model=CanvasOut, status_code=201)
def create_canvas(body: NewCanvas, request: Request, s: SessionDep) -> Any:
    if body.course_id is not None and s.get(Course, body.course_id) is None:
        raise HTTPException(422, f"no course {body.course_id!r}")
    now = _assistant(request).now()
    canvas = Canvas(title=body.title.strip(), course_id=body.course_id, scene={}, state={}, created_at=now,
                    updated_at=now)  # fmt: skip
    s.add(canvas)
    s.commit()
    return _out(request, canvas)


@router.get("/{canvas_id}", response_model=CanvasOut)
def get_canvas(canvas_id: int, request: Request, s: SessionDep) -> Any:
    return _out(request, _canvas(s, canvas_id))


class CanvasPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/{canvas_id}", response_model=CanvasSummary)
def rename_canvas(canvas_id: int, body: CanvasPatch, s: SessionDep) -> Any:
    canvas = _canvas(s, canvas_id)
    canvas.title = body.title.strip()
    s.commit()
    return canvas


class Scene(BaseModel):
    elements: list[dict[str, Any]] = Field(max_length=50_000)
    appState: dict[str, Any] = Field(default_factory=dict)  # noqa: N815 (Excalidraw's name)
    state: dict[str, Any] = Field(default_factory=dict)


@router.put("/{canvas_id}/scene", status_code=204)
def save_scene(canvas_id: int, body: Scene, request: Request, s: SessionDep) -> None:
    canvas = _canvas(s, canvas_id)
    canvas.scene = {"elements": body.elements, "appState": body.appState}
    canvas.state = body.state
    canvas.updated_at = _assistant(request).now()
    s.commit()


@router.put("/{canvas_id}/files/{file_id}", response_model=CanvasFileOut, status_code=201)
async def put_file(canvas_id: int, file_id: str, request: Request, s: SessionDep) -> Any:
    _canvas(s, canvas_id)
    mime = request.headers.get("content-type", "").split(";")[0].strip()
    content = bytearray()
    async for chunk in request.stream():
        content += chunk
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, f"canvas images can be up to {MAX_FILE_BYTES // 2**20} MB")
    try:
        saved = await asyncio.to_thread(save_file, _root(request), canvas_id, file_id, mime, bytes(content))
    except CanvasFileError as e:
        raise HTTPException(422, str(e)) from e
    return CanvasFileOut(id=saved.id, mime_type=saved.mime_type)


@router.get("/{canvas_id}/files/{file_id}")
def get_file(canvas_id: int, file_id: str, request: Request) -> FileResponse:
    found = file_path(_root(request), canvas_id, file_id)
    if found is None:
        raise HTTPException(404, "no such file")
    path, mime = found
    headers = {"Cache-Control": "private, max-age=31536000, immutable"}
    if mime == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
    return FileResponse(path, media_type=mime, headers=headers)


@router.delete("/{canvas_id}", status_code=204)
async def delete_canvas(canvas_id: int, request: Request) -> Response:
    """Deletes a canvas, and its conversation if it's the canvas's own (a Tutor session stays)."""
    with request.app.state.Session.begin() as s:
        canvas = _canvas(s, canvas_id)
        conv = s.get(Conversation, canvas.conversation_id) if canvas.conversation_id is not None else None
        s.delete(canvas)
        conv_id = conv.id if conv is not None and conv.mode == "canvas" else None
        if conv_id is not None:
            s.delete(conv)
    await asyncio.to_thread(remove_all, _root(request), canvas_id)
    if conv_id is not None:
        await _assistant(request).client.reset(f"chat:{conv_id}")
    return Response(status_code=204)


class Snapshot(BaseModel):
    """The picture of the canvas sent with a message: an upload, its size, and how much of it a note takes."""

    id: str
    width: int = Field(gt=0, le=4000)
    height: int = Field(gt=0, le=4000)
    note: tuple[int, int]


class CanvasAsk(BaseModel):
    kind: Literal["setup", "help", "chat"]
    text: str = Field("", max_length=8000)
    photos: list[str] = Field(default_factory=list, max_length=4)  # setup: photos of the problem
    snapshot: Snapshot | None = None


@router.post("/{canvas_id}/ask", response_model=ConversationOut, status_code=202)
def ask(canvas_id: int, body: CanvasAsk, request: Request, s: SessionDep) -> Any:
    """Ask the tutor from the canvas: set up the problem, press Help, or chat. Starts its conversation the
    first time."""
    assistant = _require_agent(request)
    canvas = _canvas(s, canvas_id)
    text = body.text.strip() or (HELP_TEXT if body.kind == "help" else "Here's the problem." if body.photos else "")
    if not text:
        raise HTTPException(422, "say what you'd like help with")
    photos = _photos(request, body.photos + ([body.snapshot.id] if body.snapshot else []))
    meta: dict[str, Any] = {"kind": body.kind}
    if body.snapshot:
        meta |= {"snapshot": body.snapshot.id, "size": [body.snapshot.width, body.snapshot.height],
                 "note": list(body.snapshot.note)}  # fmt: skip
    conv_id = canvas.conversation_id
    if conv_id is not None and s.get(Conversation, conv_id) is None:
        conv_id = None  # deleted from elsewhere: start over
    try:
        if conv_id is None:
            conv_id = assistant.start_conversation(text, None, canvas.course_id, "canvas", photos, meta)
            canvas.conversation_id = conv_id
            s.commit()
        else:
            assistant.add_message(conv_id, text, photos, meta)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    s.expire_all()
    return _conversation(s, conv_id)
