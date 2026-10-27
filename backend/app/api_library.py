"""The course library's API: what's in it, adding your own documents (textbooks, notes, specs), and
opening any document, at a page for PDFs."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .api import SessionDep
from .library import Fetched, Library, LibrarySync
from .models import Course, Document

router = APIRouter(prefix="/api/library")
MAX_UPLOAD = 300 * 2**20
UPLOAD_KINDS = {"textbook", "notes", "assignment", "slides", "reading", "other"}


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    course_id: str | None
    title: str
    kind: str
    url: str | None
    item_source_id: str | None
    has_file: bool
    has_text: bool
    pages: int | None
    size: int | None
    note: str | None
    updated_at: datetime


class RunOut(BaseModel):
    name: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    saved: int
    unchanged: int
    removed: int
    skipped: list[str]


class LibraryOut(BaseModel):
    documents: list[DocumentOut]
    fetchers: list[str]  # "site:cse410", "canvas"
    runs: list[RunOut]  # the last run of each, since the server started
    running: bool
    next_run_at: datetime | None


def _out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id, source=d.source, course_id=d.course_id, title=d.title, kind=d.kind, url=d.url,
        item_source_id=d.item_source_id, has_file=d.path is not None, has_text=d.text_path is not None,
        pages=d.pages, size=d.size, note=d.note, updated_at=d.updated_at,
    )  # fmt: skip


def _library(request: Request) -> Library:
    return request.app.state.library


def _sync(request: Request) -> LibrarySync:
    return request.app.state.library_sync


@router.get("", response_model=LibraryOut)
def library(request: Request, s: SessionDep) -> Any:
    sync = _sync(request)
    docs = s.scalars(select(Document).where(Document.removed_at.is_(None)).order_by(Document.course_id, Document.title))
    return LibraryOut(
        documents=[_out(d) for d in docs], fetchers=sync.fetchers, running=sync.running, next_run_at=sync.next_run_at,
        runs=[RunOut(**{**r.__dict__, "skipped": r.skipped[:50]}) for r in sync.runs.values()],
    )  # fmt: skip


@router.post("/sync", status_code=202)
async def sync_now(request: Request) -> dict[str, bool]:
    sync = _sync(request)
    if not sync.running:
        task = asyncio.create_task(sync.run_now())
        request.app.state.background.add(task)
        task.add_done_callback(request.app.state.background.discard)
    return {"running": True}


def _course(s: SessionDep, course_id: str | None) -> str | None:
    if course_id and s.get(Course, course_id) is None:
        raise HTTPException(422, f"no course {course_id!r}")
    return course_id or None


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload(
    request: Request,
    s: SessionDep,
    file: Annotated[UploadFile, File()],
    course_id: Annotated[str | None, Form()] = None,
    kind: Annotated[str, Form()] = "textbook",
    title: Annotated[str | None, Form(max_length=200)] = None,
) -> Any:
    """Add a file of your own: a textbook PDF, lecture notes, a spec that isn't online."""
    if kind not in UPLOAD_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(UPLOAD_KINDS)}")
    course = _course(s, course_id)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, f"files can be up to {MAX_UPLOAD // 2**20} MB")
    if not content:
        raise HTTPException(422, "the file is empty")
    name = file.filename or "document"
    digest = hashlib.sha256(content).hexdigest()[:16]
    doc = Fetched(
        source="upload", key=f"{course or 'general'}:{digest}", title=(title or "").strip() or re.sub(r"\.[^.]+$", "", name),
        kind=kind, rel_path=f"{digest}-{name}", course_id=course, content=content, mime=file.content_type,
    )  # fmt: skip
    return await _store(request, s, doc)


class TextIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=500_000)
    course_id: str | None = None
    kind: str = "notes"


@router.post("/text", response_model=DocumentOut, status_code=201)
async def add_text(body: TextIn, request: Request, s: SessionDep) -> Any:
    """Add context as text: what your TA said, which sections the exam covers, your own notes."""
    if body.kind not in UPLOAD_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(UPLOAD_KINDS)}")
    course = _course(s, body.course_id)
    digest = hashlib.sha256(f"{body.title}\n{body.text}".encode()).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:60]
    doc = Fetched(source="upload", key=f"{course or 'general'}:{digest}", title=body.title.strip(), kind=body.kind,
                  rel_path=f"{digest}-{slug}.md", course_id=course, text=body.text)  # fmt: skip
    return await _store(request, s, doc)


async def _store(request: Request, s: SessionDep, doc: Fetched) -> DocumentOut:
    library = _library(request)
    doc_id = await asyncio.to_thread(library.save, doc)
    await asyncio.to_thread(library.write_index, _sync(request).tz)
    return _out(s.get(Document, doc_id))


@router.delete("/{doc_id}", status_code=204)
async def delete(doc_id: int, request: Request, s: SessionDep) -> None:
    doc = s.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, f"no document {doc_id}")
    if doc.source != "upload":
        raise HTTPException(409, "only documents you added can be deleted; the others come back on the next sync")
    library = _library(request)
    await asyncio.to_thread(library.delete, doc_id)
    await asyncio.to_thread(library.write_index, _sync(request).tz)


def _doc(s: SessionDep, doc_id: int) -> Document:
    doc = s.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, f"no document {doc_id}")
    return doc


@router.get("/{doc_id}/file")
def file(doc_id: int, request: Request, s: SessionDep) -> FileResponse:
    doc = _doc(s, doc_id)
    if not doc.path:
        raise HTTPException(404, "this document is a web page; it has text but no file")
    path = _library(request).file(doc.path)
    # Inline, so PDFs open in the browser's viewer (where #page=N works).
    return FileResponse(path, media_type=doc.mime or None, filename=path.name, content_disposition_type="inline")


@router.get("/{doc_id}/text", response_class=PlainTextResponse)
def text(doc_id: int, request: Request, s: SessionDep) -> str:
    doc = _doc(s, doc_id)
    if not doc.text_path:
        raise HTTPException(404, doc.note or "no text version")
    return _library(request).file(doc.text_path).read_text(encoding="utf-8", errors="replace")


@router.get("/{doc_id}/open")
def open_doc(doc_id: int, s: SessionDep, page: int | None = None) -> RedirectResponse:
    """Where a citation should go: the PDF at that page, the web page it came from, or its text."""
    doc = _doc(s, doc_id)
    if doc.path and (doc.mime or "").startswith("application/pdf") or (doc.path or "").lower().endswith(".pdf"):
        return RedirectResponse(f"/api/library/{doc.id}/file" + (f"#page={page}" if page else ""), status_code=302)
    if doc.url and doc.source != "upload":
        return RedirectResponse(doc.url, status_code=302)
    if doc.path:
        return RedirectResponse(f"/api/library/{doc.id}/file", status_code=302)
    return RedirectResponse(f"/api/library/{doc.id}/text", status_code=302)
