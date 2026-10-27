"""Plans, briefs and conversations as the agent saves them and the API shows them."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Resource(BaseModel):
    title: str
    doc_id: int | None = None  # a library document
    pages: str | None = None
    url: str | None = None  # or a web page


class PlanTask(BaseModel):
    title: str
    minutes: int | None = None
    course: str | None = None
    item_id: int | None = None  # the item it moves forward
    when: str | None = None
    why: str | None = None
    steps: list[str] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    done: bool = False


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: str
    status: str  # running, ready, error
    note: str | None
    summary: str | None
    tasks: list[PlanTask]
    heads_up: list[str]
    answer: str | None
    error: str | None
    model: str | None
    cost: float | None
    seconds: float | None
    created_at: datetime
    finished_at: datetime | None


class BriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    title: str
    status: str
    markdown: str | None
    minutes: int | None
    resources: list[Resource]
    answer: str | None
    error: str | None
    cost: float | None
    created_at: datetime
    finished_at: datetime | None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    status: str
    text: str
    photos: list[str] = Field(default_factory=list)  # ids: /api/uploads/<id>
    canvas: dict[str, Any] | None = None  # from the Canvas tab: {"kind", "snapshot", "size", "note"}
    error: str | None
    cost: float | None
    created_at: datetime

    @field_validator("photos", mode="before")
    @classmethod
    def _none_is_empty(cls, v: list[str] | None) -> list[str]:
        return v or []


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    item_id: int | None
    course_id: str | None
    mode: str
    canvas_id: int | None = None  # its canvas (api_canvas.py), if it has opened one
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut] = Field(default_factory=list)
