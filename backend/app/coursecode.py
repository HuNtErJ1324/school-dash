"""Course codes: finding them in free text and turning them into ids."""

from __future__ import annotations

import re

# UW department codes can contain spaces ("E E 215", "A A 210"). Case-insensitive because
# Gradescope short names are typed by course staff ("Math300 Au26").
_CODE = re.compile(r"\b([A-Z][A-Z&]*(?: [A-Z&]+)*) ?(\d{3}[A-Z]?)\b", re.I)


def parse_course_code(text: str | None) -> str | None:
    """'CSE 410 A Au 26: Introduction To Operating Systems' -> 'CSE 410'; 'Math300' -> 'MATH 300'."""
    if not text:
        return None
    m = _CODE.search(text)
    return f"{m.group(1).upper()} {m.group(2).upper()}" if m else None


def course_slug(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower())
