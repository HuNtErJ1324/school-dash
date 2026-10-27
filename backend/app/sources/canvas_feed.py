"""Canvas's calendar feed: the secret .ics link at the bottom of Canvas -> Calendar ("Calendar Feed").

Needs no API token and covers every course's assignments and calendar events, but has no
submission status or grades. The `canvas` source has those, if you can authenticate to the API.
Assignment ids match the `canvas` source's ("assignment:<id>"), so the two can be merged later.
"""

from __future__ import annotations

import re
from datetime import time, timedelta
from urllib.parse import urlsplit

from pydantic import BaseModel

from ..coursecode import parse_course_code
from .base import CourseRef, FetchResult, ItemIn, Source, SourceContext, register
from .classify import kind_from_title
from .ics import due_from, expand, read_feed, unique_ids

_UID = re.compile(r"event-(assignment(?:-override)?|calendar-event)-(\d+)")
_CONTEXT = re.compile(r"\s*\[([^\[\]]+)\]\s*$")  # "HW1 [MATH 208 A]", or "[CSE 312 A Au 26: Foundations ...]"
_COURSE_IN_URL = re.compile(r"include_contexts=course_(\d+)")


class CanvasFeedConfig(BaseModel):
    url: str  # keep it in .env: anyone with this URL can read your calendar
    default_due_time: time = time(23, 59)
    lookback_days: float = 14
    lookahead_days: float = 150


@register
class CanvasFeedSource(Source[CanvasFeedConfig]):
    type = "canvas_feed"
    Config = CanvasFeedConfig
    default_interval = timedelta(minutes=30)

    async def fetch(self, ctx: SourceContext) -> FetchResult:
        cfg = self.config
        start, end = ctx.window(cfg.lookback_days, cfg.lookahead_days)
        feed = urlsplit(cfg.url)
        items: list[ItemIn] = []
        courses: dict[int | str, CourseRef] = {}

        for o in expand(await read_feed(ctx, cfg.url), start, end, ctx.tz):
            title, context = o.title, None
            if m := _CONTEXT.search(title):
                title, context = title[: m.start()].strip(), m.group(1).strip()

            in_url = _COURSE_IN_URL.search(o.url or "")
            canvas_id = int(in_url.group(1)) if in_url else None
            course = None
            if context or canvas_id:
                # The tag is usually just "MATH 208 A"; only the long form carries a real name.
                name = context if context and ":" in context else None
                links = {"canvas": f"{feed.scheme}://{feed.netloc}/courses/{canvas_id}"} if canvas_id and feed.netloc else {}
                course = CourseRef(code=parse_course_code(context), canvas_id=canvas_id, name=name, links=links)
                courses[canvas_id or context] = course

            uid = _UID.search(o.uid)
            if uid and uid.group(1).startswith("assignment"):
                url = o.url
                if canvas_id and feed.netloc:
                    url = f"{feed.scheme}://{feed.netloc}/courses/{canvas_id}/assignments/{uid.group(2)}"
                kind = kind_from_title(title)
                due = due_from(o, cfg.default_due_time, ctx.tz)
                items.append(ItemIn(
                    source_id=f"assignment:{uid.group(2)}", kind=kind, title=title, url=url, course=course,
                    due_at=due, starts_at=due if kind == "exam" else None,  # exams also belong on the day's agenda
                    all_day=o.all_day, raw=o.props,
                ))  # fmt: skip
            else:
                source_id = f"calendar_event:{uid.group(2)}" if uid else o.uid
                items.append(ItemIn(
                    source_id=source_id, kind="event", title=title, url=o.url, course=course,
                    starts_at=o.start, ends_at=o.end, all_day=o.all_day, location=o.location,
                    raw=o.props,
                ))  # fmt: skip

        return FetchResult(items=unique_ids(items), courses=list(courses.values()), window=(start, end))
