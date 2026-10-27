"""The contract every source adapter implements, and the registry that finds adapters by type."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, Field

Kind = Literal[
    "assignment", "quiz", "exam", "discussion", "announcement", "release",
    "lecture", "section", "office_hours", "event", "other",
]  # fmt: skip
Status = Literal["pending", "submitted", "graded", "missing", "excused"]


class CourseRef(BaseModel):
    """How a source names a course. Sync matches it to a course by canvas_id, then by code."""

    code: str | None = None
    canvas_id: int | None = None
    name: str | None = None
    links: dict[str, str] = Field(default_factory=dict)  # e.g. {"canvas": ".../courses/123"}; config.yaml's win
    term: str | None = None  # the quarter, "26au", when the source knows it


class ItemIn(BaseModel):
    source_id: str  # must stay the same across syncs; that's what upserts key on
    kind: Kind
    title: str
    url: str | None = None
    course: CourseRef | None = None
    due_at: AwareDatetime | None = None  # deadlines
    starts_at: AwareDatetime | None = None  # things that happen at a time
    ends_at: AwareDatetime | None = None
    all_day: bool = False  # the source gave a date only; the time was filled in
    location: str | None = None
    status: Status | None = None  # None when unknown or there's nothing to submit
    points: float | None = None  # points possible
    score: float | None = None  # points earned, once graded
    category: str | None = None  # the source's own grouping (Canvas assignment group), if any
    raw: dict[str, Any] = Field(default_factory=dict)


class FetchResult(BaseModel):
    items: list[ItemIn]
    courses: list[CourseRef] = Field(default_factory=list)
    # The time range this fetch covered. Stored items outside it aren't marked removed just
    # because the source didn't return them. None means the fetch saw everything.
    window: tuple[AwareDatetime, AwareDatetime] | None = None


class SourceError(Exception):
    """A failure whose message is safe and useful to show in the dashboard as-is."""


@dataclass
class SourceContext:
    tz: ZoneInfo
    http: httpx.AsyncClient
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def window(self, lookback_days: float, lookahead_days: float) -> tuple[datetime, datetime]:
        now = self.now()
        return now - timedelta(days=lookback_days), now + timedelta(days=lookahead_days)


class Source[C: BaseModel](ABC):
    type: ClassVar[str]
    Config: ClassVar[type[BaseModel]]
    default_interval: ClassVar[timedelta] = timedelta(hours=1)

    def __init__(self, name: str, config: C, interval: timedelta | None = None):
        self.name = name
        self.config = config
        self.interval = interval or self.default_interval

    @abstractmethod
    async def fetch(self, ctx: SourceContext) -> FetchResult: ...


REGISTRY: dict[str, type[Source[Any]]] = {}


def register[S: type[Source[Any]]](cls: S) -> S:
    REGISTRY[cls.type] = cls
    return cls


def describe_url(url: str) -> str:
    """Host only, for error messages: feed URLs often carry a secret in the path."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/…" if parts.netloc else "the configured file"
