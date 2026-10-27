"""UW quarters: codes like "26au", names like "Autumn 2026", and which quarter a course or date is in.

Sources say which quarter a course belongs to when they can (Canvas's term "Autumn 2026", Gradescope's
"Fall 2026", a course site under /26au/). For a course none of them place, its items' dates decide.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Course, Item

SEASONS = {"wi": "Winter", "sp": "Spring", "su": "Summer", "au": "Autumn"}
_ORDER = {"wi": 0, "sp": 1, "su": 2, "au": 3}
_WORDS = {"autumn": "au", "fall": "au", "winter": "wi", "spring": "sp", "summer": "su"}
_TERM = re.compile(r"\b(autumn|fall|winter|spring|summer)\s+(\d{4})\b", re.I)
_URL = re.compile(r"/(\d{2}(?:wi|sp|su|au))(?:/|$)")
CODE = re.compile(r"^\d{2}(wi|sp|su|au)$")
# Where each quarter's dates fall, as (month, day) starts. Breaks go to the quarter before.
_STARTS = [((1, 1), "wi"), ((4, 1), "sp"), ((6, 16), "su"), ((9, 1), "au")]


def code_from_term(text: str | None) -> str | None:
    """"Autumn 2026" or "Fall 2026" -> "26au". None for terms like "2025-2026 101"."""
    m = _TERM.search(text or "")
    return f"{int(m.group(2)) % 100:02d}{_WORDS[m.group(1).lower()]}" if m else None


def code_from_url(url: str | None) -> str | None:
    """A UW course site's quarter: .../courses/cse410/26au/calendar/ -> "26au"."""
    m = _URL.search(url or "")
    return m.group(1) if m else None


def name(code: str) -> str:
    return f"{SEASONS[code[2:]]} 20{code[:2]}"


def sort_key(code: str) -> tuple[int, int]:
    return int(code[:2]), _ORDER[code[2:]]


def quarter_of(d: date) -> str:
    season = next(s for (start, s) in reversed(_STARTS) if (d.month, d.day) >= start)
    return f"{d.year % 100:02d}{season}"


def bounds(code: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The dates quarter_of gives this quarter, as [start, end) in local time."""
    year = 2000 + int(code[:2])
    i = next(n for n, (_, s) in enumerate(_STARTS) if s == code[2:])
    (m, d), _ = _STARTS[i]
    start = datetime.combine(date(year, m, d), time(), tz)
    if i + 1 < len(_STARTS):
        (m2, d2), _ = _STARTS[i + 1]
        end = datetime.combine(date(year, m2, d2), time(), tz)
    else:
        end = datetime.combine(date(year + 1, 1, 1), time(), tz)
    return start, end


def course_terms(s: Session, tz: ZoneInfo) -> dict[str, str]:
    """Each course's quarter: the one its sources or config.yaml give, or else where most of its dates fall."""
    terms = {c.id: c.term for c in s.scalars(select(Course)) if c.term}
    votes: dict[str, Counter[str]] = {}
    rows = s.execute(
        select(Item.course_id, Item.at).where(Item.course_id.is_not(None), Item.at.is_not(None), Item.removed_at.is_(None))
    )  # fmt: skip
    for course_id, at in rows:
        if course_id not in terms:
            votes.setdefault(course_id, Counter())[quarter_of(at.astimezone(tz).date())] += 1
    for course_id, counter in votes.items():
        terms[course_id] = counter.most_common(1)[0][0]
    return terms


def current(terms: dict[str, str], today: date) -> str:
    """The quarter the dashboard opens on: today's, or between quarters (before next quarter's courses
    show up anywhere) the latest one that has courses."""
    now = quarter_of(today)
    if now in terms.values() or not terms:
        return now
    earlier = [t for t in terms.values() if sort_key(t) <= sort_key(now)]
    return max(earlier or terms.values(), key=sort_key)


def later(a: str | None, b: str | None) -> str | None:
    """The later of two quarters (a course seen in two keeps the newer one)."""
    if not a or not b:
        return a or b
    return max(a, b, key=sort_key)

