"""Made-up items dated relative to today, for trying the dashboard without any accounts."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from .base import CourseRef, FetchResult, ItemIn, Source, SourceContext, register


class DemoConfig(BaseModel):
    pass


@register
class DemoSource(Source[DemoConfig]):
    type = "demo"
    Config = DemoConfig
    default_interval = timedelta(hours=1)

    async def fetch(self, ctx: SourceContext) -> FetchResult:
        today = ctx.now().astimezone(ctx.tz).replace(hour=0, minute=0, second=0, microsecond=0)

        def at(days: int, hour: int, minute: int = 0) -> datetime:
            return (today + timedelta(days=days)).replace(hour=hour, minute=minute)

        demo101 = CourseRef(code="DEMO 101", name="Intro to Demonstrations")
        demo202 = CourseRef(code="DEMO 202", name="Systems for Pretending")
        demo303 = CourseRef(code="DEMO 303", name="Advanced Placeholders")

        def due(sid: str, course: CourseRef, title: str, when: datetime, **kw) -> ItemIn:
            return ItemIn(source_id=sid, kind=kw.pop("kind", "assignment"), title=title, course=course, due_at=when, **kw)

        def event(sid: str, course: CourseRef, kind, title: str, start: datetime, minutes: int, where: str) -> ItemIn:
            return ItemIn(source_id=sid, kind=kind, title=title, course=course, starts_at=start,
                          ends_at=start + timedelta(minutes=minutes), location=where)  # fmt: skip

        items = [
            due("rr4", demo101, "Reading response 4", at(-1, 23, 59), status="pending", points=5),
            due("q2", demo202, "Quiz 2", at(-1, 23, 59), kind="quiz", status="missing", points=10),
            due("ps3", demo101, "Problem set 3", at(0, 23, 59), status="pending", points=20),
            due("lab2", demo202, "Lab 2: Shell", at(1, 18), status="pending", points=50),
            due("prop", demo303, "Project proposal", at(2, 23, 59), status="submitted", points=10),
            due("lab3", demo303, "Lab 3", at(6, 23, 59), all_day=True),
            due("ps4", demo101, "Problem set 4", at(8, 23, 59), status="pending", points=20),
            due("lab3b", demo202, "Lab 3: Allocator", at(11, 18), status="pending", points=50),
            event("mid", demo303, "exam", "Midterm", at(4, 11, 30), 50, "Room 120"),
            event("lec1", demo101, "lecture", "Lecture: Recursion", at(0, 9, 30), 50, "Room 101"),
            event("lec2", demo202, "lecture", "Lecture: Virtual memory", at(1, 13, 30), 80, "Room 202"),
            event("oh1", demo202, "office_hours", "OH (TA)", at(0, 15), 60, "Lab 3rd floor"),
            # Earlier, graded work, so the grades widget has something to show.
            due("ps1", demo101, "Problem set 1", at(-15, 23, 59), status="graded", points=20, score=18),
            due("ps2", demo101, "Problem set 2", at(-8, 23, 59), status="graded", points=20, score=15.5),
            due("rr3", demo101, "Reading response 3", at(-8, 23, 59), status="graded", points=5, score=5),
            due("lab1", demo202, "Lab 1: Warmup", at(-12, 18), status="graded", points=50, score=46),
            due("q1", demo202, "Quiz 1", at(-9, 23, 59), kind="quiz", status="graded", points=10, score=7),
        ]
        return FetchResult(items=items, courses=[demo101, demo202, demo303])
