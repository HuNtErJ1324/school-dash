"""Generic iCalendar (.ics) feeds, and the helpers the more specific calendar sources build on."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events
import x_wr_timezone
from pydantic import BaseModel

from .base import (
    CourseRef, FetchResult, ItemIn, Kind, Source, SourceContext, SourceError, describe_url, register,
)  # fmt: skip


@dataclass
class Occurrence:
    """One concrete happening of a VEVENT; repeating events are already expanded."""

    uid: str
    summary: str
    description: str
    location: str | None
    url: str | None
    start: datetime  # always timezone-aware
    end: datetime | None
    all_day: bool
    recurrence_id: datetime | None  # set when this is one instance of a repeating event
    props: dict[str, str]  # every property as text, for item.raw and source-specific fields

    @property
    def title(self) -> str:
        return self.summary.split("\n", 1)[0].strip()


async def read_feed(ctx: SourceContext, url: str) -> str:
    """Fetch an http(s)/webcal URL, or read a local file (handy for testing a feed)."""
    if url.startswith("webcal://"):
        url = "https://" + url.removeprefix("webcal://")
    if not url.startswith(("http://", "https://")):
        try:
            return Path(url).read_text(encoding="utf-8")
        except OSError as e:
            raise SourceError(f"can't read {url}: {e.strerror}") from e
    try:
        resp = await ctx.http.get(url)
    except httpx.HTTPError as e:
        raise SourceError(f"couldn't reach {describe_url(url)} ({type(e).__name__})") from e
    if resp.status_code != 200:
        raise SourceError(f"{describe_url(url)} returned HTTP {resp.status_code}")
    return resp.text


def expand(text: str, start: datetime, end: datetime, tz: ZoneInfo) -> list[Occurrence]:
    """Every event occurrence overlapping [start, end], sorted by start time."""
    try:
        cal = icalendar.Calendar.from_ical(text)
    except ValueError as e:
        raise SourceError("the feed isn't valid iCalendar data") from e
    cal = x_wr_timezone.to_standard(cal)  # floating times + X-WR-TIMEZONE -> that timezone
    # Expansion stamps a RECURRENCE-ID on every occurrence, one-off events included,
    # so remember which events really repeat.
    repeating = {str(c.get("UID", "")) for c in cal.walk("VEVENT") if "RRULE" in c or "RDATE" in c}

    out = []
    # Pad by a day: the library judges all-day dates in the bounds' timezone, which is UTC here,
    # so an Oct 16 deadline counts as over at 5pm Pacific that day. Without the pad it drops out
    # of a window it's still inside, and sync marks it removed.
    for ev in recurring_ical_events.of(cal).between(start - timedelta(days=1), end + timedelta(days=1)):
        uid = str(ev.get("UID", ""))
        ev_start, all_day = _aware(ev.decoded("DTSTART"), tz)
        if "DTEND" in ev:
            ev_end = _aware(ev.decoded("DTEND"), tz)[0]
        elif "DURATION" in ev:
            ev_end = ev_start + ev.decoded("DURATION")
        else:
            ev_end = None
        rid = None
        if uid in repeating and "RECURRENCE-ID" in ev:
            rid = _aware(ev.decoded("RECURRENCE-ID"), tz)[0]
        out.append(Occurrence(
            uid=uid,
            summary=str(ev.get("SUMMARY", "")).strip(),
            description=str(ev.get("DESCRIPTION", "")).strip(),
            location=str(ev["LOCATION"]) if "LOCATION" in ev else None,
            url=str(ev["URL"]) if "URL" in ev else None,
            start=ev_start,
            end=ev_end,
            all_day=all_day,
            recurrence_id=rid,
            props={k: _prop_text(v) for k, v in ev.items()},
        ))  # fmt: skip
    out.sort(key=lambda o: (o.start, o.summary))
    return out


def _aware(value: date | datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    """A DTSTART-style value as an aware datetime, and whether it was a bare date."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=tz)), False
    return datetime.combine(value, time(0), tz), True


def _prop_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_prop_text(v) for v in value)
    if isinstance(value, str):
        return str(value)
    try:
        return value.to_ical().decode("utf-8", "replace")
    except AttributeError:
        return str(value)


def due_from(o: Occurrence, default_due_time: time, tz: ZoneInfo) -> datetime:
    """A deadline's due time. All-day deadlines get default_due_time on that day."""
    if not o.all_day:
        return o.start
    return datetime.combine(o.start.date(), default_due_time, tz)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")


def unique_ids(items: list[ItemIn]) -> list[ItemIn]:
    """Suffix repeated source_ids (#2, #3, ...) so a single fetch never collides with itself."""
    seen: dict[str, int] = {}
    for item in items:
        seen[item.source_id] = n = seen.get(item.source_id, 0) + 1
        if n > 1:
            item.source_id = f"{item.source_id}#{n}"
    return items


class IcsConfig(BaseModel):
    url: str  # http(s)/webcal URL, or a local file path
    course: str | None = None  # course code every event belongs to, e.g. "CSE 410"
    kind: Kind = "event"
    deadlines: bool = False  # each event is a due date (e.g. an assignments calendar)
    # "uid" trusts the feed's UIDs. "content" builds ids from the title (deadlines) or
    # title + start (events), for feeds that regenerate UIDs whenever they're rebuilt.
    id_from: Literal["uid", "content"] = "uid"
    default_due_time: time = time(23, 59)  # for deadlines listed as all-day
    lookback_days: float = 7
    lookahead_days: float = 120


@register
class IcsSource(Source[IcsConfig]):
    type = "ics"
    Config = IcsConfig
    default_interval = timedelta(hours=6)

    async def fetch(self, ctx: SourceContext) -> FetchResult:
        cfg = self.config
        start, end = ctx.window(cfg.lookback_days, cfg.lookahead_days)
        course = CourseRef(code=cfg.course) if cfg.course else None
        items = []
        for o in expand(await read_feed(ctx, cfg.url), start, end, ctx.tz):
            when = o.recurrence_id or o.start
            if cfg.id_from == "uid":
                source_id = f"{o.uid}/{stamp(when)}" if o.recurrence_id else o.uid
            else:
                source_id = slug(o.title) if cfg.deadlines else f"{slug(o.title)}/{stamp(when)}"

            common: dict[str, Any] = dict(
                source_id=source_id, kind=cfg.kind, title=o.title, url=o.url, course=course,
                all_day=o.all_day, location=o.location, raw=o.props,
            )  # fmt: skip
            if cfg.deadlines:
                items.append(ItemIn(**common, due_at=due_from(o, cfg.default_due_time, ctx.tz)))
            else:
                items.append(ItemIn(**common, starts_at=o.start, ends_at=o.end))
        return FetchResult(items=unique_ids(items), window=(start, end))
