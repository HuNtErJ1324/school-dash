"""UW CSE course calendars built with createcal: the feeds listed on a course's
"Subscribe to this calendar" page (calendar/subscribe.html).

createcal tags each event with X-CREATECAL-EVENTTYPE (lecture, section, oh, exam, hw),
regenerates every UID on each rebuild, and lists most deadlines as all-day events
("410 Lab 1 Due"), sometimes with a time in the title ("(due 6pm PDT)").
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .base import CourseRef, FetchResult, ItemIn, Kind, Source, SourceContext, register
from ..quarters import code_from_url
from .ics import Occurrence, due_from, expand, read_feed, slug, stamp, unique_ids

_TYPE_KINDS: dict[str, Kind] = {
    "lecture": "lecture",
    "section": "section",
    "oh": "office_hours",
    "exam": "exam",
    "quiz": "quiz",
}
_DUE = re.compile(r"\bdue\b", re.I)
_RELEASE = re.compile(r"\b(releases?d?|out)\b", re.I)
_DUE_TIME = re.compile(r"\(\s*due\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?[^)]*\)", re.I)
_TRAILING_DUE = re.compile(r"\s+due$", re.I)
_NOT_A_TOPIC = re.compile(r"^(reading|materials|slides)\s*:", re.I)


class CseCalendarConfig(BaseModel):
    course: str  # "CSE 410"
    base_url: str  # the course's calendar directory, e.g. .../courses/cse410/26au/calendar/
    calendars: list[str]  # from the subscribe page's calendar-<name>.ics links: lecture-A, oh, section-AA
    default_due_time: time = time(23, 59)  # most deadlines are listed as all-day
    lookback_days: float = 7
    lookahead_days: float = 120


@register
class CseCalendarSource(Source[CseCalendarConfig]):
    type = "cse_calendar"
    Config = CseCalendarConfig
    default_interval = timedelta(hours=6)

    async def fetch(self, ctx: SourceContext) -> FetchResult:
        cfg = self.config
        start, end = ctx.window(cfg.lookback_days, cfg.lookahead_days)
        base = cfg.base_url.rstrip("/") + "/"
        items = []
        for name in cfg.calendars:
            text = await read_feed(ctx, f"{base}calendar-{name}.ics")
            items += [self._item(o, base, ctx.tz) for o in expand(text, start, end, ctx.tz)]
        return FetchResult(items=unique_ids(items), window=(start, end))

    def _item(self, o: Occurrence, base: str, tz: ZoneInfo) -> ItemIn:
        cfg = self.config
        etype = o.props.get("X-CREATECAL-EVENTTYPE", "").lower()
        number = cfg.course.split()[-1]
        title = re.sub(rf"^(?:[A-Za-z]+ ?)?{re.escape(number)}\s+", "", o.title)  # "410 Lab 1 Due" -> "Lab 1 Due"
        common = dict(course=CourseRef(code=cfg.course, term=code_from_url(base)), url=base + "calendar.html", raw=o.props)

        if etype == "hw" and _DUE.search(title):
            due, all_day = due_from(o, cfg.default_due_time, tz), o.all_day
            if m := _DUE_TIME.search(title):
                hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
                due, all_day = datetime.combine(o.start.date(), time(hour, int(m.group(2) or 0)), tz), False
                title = _DUE_TIME.sub("", title).strip()
            title = _TRAILING_DUE.sub("", title)
            # Keyed on the title alone, so a deadline that moves keeps its identity.
            return ItemIn(**common, source_id=f"due:{slug(title)}", kind="assignment", title=title,
                          due_at=due, all_day=all_day)  # fmt: skip

        kind = "release" if etype == "hw" and _RELEASE.search(title) else _TYPE_KINDS.get(etype, "event")
        if kind in ("lecture", "section") and (topic := _topic(o.description)):
            title = f"{title}: {topic}"
        return ItemIn(
            **common,
            source_id=f"{slug(o.title)}/{stamp(o.recurrence_id or o.start)}",
            kind=kind,
            title=title,
            starts_at=o.start,
            ends_at=o.end,
            all_day=o.all_day,
            location=o.location,
        )


def _topic(description: str) -> str | None:
    first = description.split("\n", 1)[0].strip()
    return first if first and not _NOT_A_TOPIC.match(first) else None
