from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from .db import Base, UTCDateTime


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(primary_key=True)  # slug of the code: "cse410"
    code: Mapped[str]  # "CSE 410"
    name: Mapped[str | None]
    color: Mapped[str | None]
    color_locked: Mapped[bool] = mapped_column(default=False)  # set in config.yaml: never reassigned
    canvas_id: Mapped[int | None] = mapped_column(unique=True)
    links: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    term: Mapped[str | None]  # the quarter, "26au", when a source or config.yaml says (see quarters.py)


class Item(Base):
    """Anything with a date: a deadline, a lecture, an exam, office hours."""

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("source", "source_id"),
        Index("ix_items_at", "at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]  # the source's configured name, e.g. "canvas-feed"
    source_id: Mapped[str]  # stable id within that source
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id"))
    kind: Mapped[str]
    title: Mapped[str]
    url: Mapped[str | None]
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # due_at or starts_at; what widgets sort on
    all_day: Mapped[bool] = mapped_column(default=False)
    location: Mapped[str | None]
    status: Mapped[str | None]
    points: Mapped[float | None]  # points possible
    score: Mapped[float | None]  # points earned, once graded
    category: Mapped[str | None]  # the source's grouping, e.g. a Canvas assignment group
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Items that are the same thing seen by different sources share a group_id: the
    # lowest item id among them. Recomputed after every sync (see dedupe.py).
    group_id: Mapped[int | None] = mapped_column(index=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # source stopped returning it

    course: Mapped[Course | None] = relationship(lazy="raise")
    overlay: Mapped[Overlay | None] = relationship(lazy="raise", uselist=False)

    # Your own state, flattened for the API. Syncs never write these.
    @property
    def done(self) -> bool:
        return bool(self.overlay and self.overlay.done)

    @property
    def hidden(self) -> bool:
        return bool(self.overlay and self.overlay.hidden)

    @property
    def pinned(self) -> bool:
        return bool(self.overlay and self.overlay.pinned)

    @property
    def note(self) -> str | None:
        return self.overlay.note if self.overlay else None


class Overlay(Base):
    """What you've said about an item: done, hidden, pinned, a note. Kept apart from synced data."""

    __tablename__ = "overlays"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    done: Mapped[bool] = mapped_column(default=False)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    hidden: Mapped[bool] = mapped_column(default=False)
    pinned: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str | None]
    separate: Mapped[bool] = mapped_column(default=False)  # never merge this with look-alikes
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Board(Base):
    """A saved dashboard: which widgets, their options, and where they sit at each screen size."""

    __tablename__ = "boards"

    name: Mapped[str] = mapped_column(primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Document(Base):
    """A course document in the library: a spec, slides, a syllabus, a textbook you uploaded.

    The original file (if any) and a plain-text version sit under the library folder; paths here
    are relative to it. The agent reads them there, and the text version is what it searches.
    """

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]  # "site", "canvas" or "upload"
    key: Mapped[str]  # stable within the source: a URL, "file:123", "assignment:45", ...
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id"))
    title: Mapped[str]
    kind: Mapped[str]  # slides, assignment, syllabus, page, reference, textbook, notes, file
    url: Mapped[str | None]  # where it lives online
    item_source_id: Mapped[str | None]  # the item it describes, e.g. Canvas's "assignment:45"
    path: Mapped[str | None]  # the original file; None when the document is a web page
    text_path: Mapped[str | None]  # markdown text; PDF pages are marked "--- page N ---"
    mime: Mapped[str | None]
    size: Mapped[int | None]
    pages: Mapped[int | None]
    chars: Mapped[int | None]
    sha256: Mapped[str | None]
    version: Mapped[str | None]  # ETag, Last-Modified or Canvas updated_at: unchanged means no re-download
    note: Mapped[str | None]  # why there's no text, if there isn't
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)  # content last changed
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    course: Mapped[Course | None] = relationship(lazy="raise")


class Plan(Base):
    """A day's plan from the agent. Re-planning adds a new one; the latest for a date is the plan."""

    __tablename__ = "plans"
    __table_args__ = (Index("ix_plans_date", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str]  # YYYY-MM-DD, local
    status: Mapped[str]  # running, ready, error
    note: Mapped[str | None]  # what you asked for when re-planning
    summary: Mapped[str | None]
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    heads_up: Mapped[list[str]] = mapped_column(JSON, default=list)
    answer: Mapped[str | None]  # the agent's closing message
    error: Mapped[str | None]
    model: Mapped[str | None]
    cost: Mapped[float | None]
    seconds: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Brief(Base):
    """The agent's guide to one assignment: what it asks, what to read first, steps, pitfalls."""

    __tablename__ = "briefs"
    __table_args__ = (Index("ix_briefs_item", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int]  # the item's group id, as the API shows it
    title: Mapped[str]
    status: Mapped[str]  # running, ready, error
    markdown: Mapped[str | None]
    minutes: Mapped[int | None]  # the agent's estimate of the whole assignment
    resources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    answer: Mapped[str | None]
    error: Mapped[str | None]
    model: Mapped[str | None]
    cost: Mapped[float | None]
    seconds: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    item_id: Mapped[int | None]  # asked from an item's menu, or the item a tutoring session is about
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id"))
    mode: Mapped[str] = mapped_column(default="ask")  # ask, explain, quiz, review (see agent/prompts.py)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    messages: Mapped[list[Message]] = relationship(lazy="raise", order_by="Message.id", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str]  # user, assistant
    status: Mapped[str]  # ready, running, error
    text: Mapped[str]
    photos: Mapped[list[str] | None] = mapped_column(JSON)  # ids of attached photos (see uploads.py)
    # Sent from a canvas: what the student did (setup, help, chat) and the snapshot of the canvas sent with it.
    canvas: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None]
    cost: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Canvas(Base):
    """A whiteboard shared with the tutor: one of the Canvas tab's, with a conversation of its own (mode
    "canvas"), or the one a Tutor session opened beside its chat. The drawing is Excalidraw's scene; its
    images are files under data/canvases/<id>/ (canvases.py)."""

    __tablename__ = "canvases"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id"))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    scene: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"elements": [...], "appState": {...}}
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # the page's own: replies drawn, snapshot frames
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


# A conversation's canvas, if it has one: the Tutor page shows which sessions have one.
Conversation.canvas_id = column_property(
    select(Canvas.id).where(Canvas.conversation_id == Conversation.id).limit(1).correlate_except(Canvas).scalar_subquery()
)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_source_started", "source", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ok: Mapped[bool]
    error: Mapped[str | None]
    items_seen: Mapped[int] = mapped_column(default=0)
