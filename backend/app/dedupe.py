"""Recognising one piece of work seen by several sources: Canvas, Gradescope, a course calendar.

Two items are the same thing when they're in the same course and either
  - have the same source_id (canvas and canvas_feed both say "assignment:<canvas id>"), or
  - are deadlines from different sources whose titles name the same work (same numbers, and
    one title's words contain the other's, after "HW"/"Homework"-style synonyms) and whose
    due dates are within a day and a half (exact title matches are paired first), or
  - one is undated graded work (an in-class quiz on Gradescope) whose title exactly matches
    the only dated entry with that title.
A group never holds two items from one source, so near-misses can't chain together. Items
you've marked "keep separate" are never merged. A group's id is its lowest item id.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Item, Overlay

DEADLINE_KINDS = {"assignment", "quiz", "exam", "discussion"}
WINDOW = timedelta(hours=36)

_SYNONYMS = [
    (r"\bproblem\s+sets?\b", "pset"),
    (r"\bp\.?s\.?(?=\s*\d)", "pset"),
    (r"\bhomeworks?\b", "hw"),
    (r"\bexercises?\b", "ex"),
    (r"\bprojects?\b", "proj"),
    (r"\bdesign\s+doc(ument)?s?\b", "dd"),
    (r"\bmid-?term\s+exam\b", "midterm"),
    (r"\bfinal\s+exam\b", "finalexam"),  # one token, so "Final Exam" never matches "Final project"
]
_STOPWORDS = {"the", "a", "an", "of", "for", "and", "to", "on", "in", "due", "submission", "submit", "upload"}


def signature(title: str) -> tuple[tuple[int, ...], frozenset[str]]:
    """The numbers in a title, in order, and its meaningful words."""
    t = re.sub(r"\([^)]*\)", " ", title.lower())  # "(due 6pm PDT)"
    for pattern, replacement in _SYNONYMS:
        t = re.sub(pattern, replacement, t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)  # "hw3" -> "hw 3"
    tokens = re.findall(r"[a-z]+|\d+", t)
    numbers = tuple(int(x) for x in tokens if x.isdigit())
    words = frozenset(w for w in tokens if not w.isdigit() and w not in _STOPWORDS)
    return numbers, words


def match_strength(a: str, b: str) -> int:
    """2: the same work by the same words ("PSet 1", "Problem Set 1"). 1: one title's words
    contain the other's ("Lab 1", "Lab 1 Questions"). 0: different work."""
    (na, wa), (nb, wb) = signature(a), signature(b)
    if na != nb:
        return 0
    if wa == wb:
        return 2 if (wa or na) else 0  # but not two titles with nothing in them
    return 1 if wa and wb and (wa <= wb or wb <= wa) else 0


def same_work(a: str, b: str) -> bool:
    return match_strength(a, b) > 0


def regroup(s: Session) -> None:
    """Recompute group_id for every live item."""
    rows = s.execute(
        select(Item.id, Item.source, Item.source_id, Item.course_id, Item.kind, Item.title, Item.at, Item.group_id)
        .where(Item.removed_at.is_(None))
    ).all()
    separate = set(s.scalars(select(Overlay.item_id).where(Overlay.separate.is_(True))))

    parent = {r.id: r.id for r in rows}
    members: dict[int, set[str]] = {r.id: {r.source} for r in rows}  # sources in each group

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb or members[ra] & members[rb]:
            return  # already together, or would put two items from one source in a group
        keep, drop = min(ra, rb), max(ra, rb)
        parent[drop] = keep
        members[keep] |= members.pop(drop)

    candidates = [r for r in rows if r.course_id and r.id not in separate]

    by_id: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in candidates:
        by_id[(r.course_id, r.source_id)].append(r.id)
    for ids in by_id.values():
        for other in ids[1:]:
            union(ids[0], other)

    by_course: dict[str, list] = defaultdict(list)
    for r in candidates:
        if r.kind in DEADLINE_KINDS and r.at is not None:
            by_course[r.course_id].append(r)
    # Collect every plausible pair first and join the strongest, closest ones first, so
    # "Lab 1" pairs with "Lab 1" before "Lab 1 Questions" can claim it.
    pairs = []
    for entries in by_course.values():
        entries.sort(key=lambda r: r.at)
        for i, a in enumerate(entries):
            for b in entries[i + 1 :]:
                if b.at - a.at > WINDOW:
                    break
                if a.source != b.source and (strength := match_strength(a.title, b.title)):
                    pairs.append((-strength, (b.at - a.at).total_seconds(), a.id, b.id))
    for *_, a_id, b_id in sorted(pairs):
        union(a_id, b_id)

    # Undated work (a quiz or exam graded on Gradescope with no dates) joins the dated entry
    # with exactly its title in the same course, when there's only one such entry to join.
    for r in candidates:
        if r.kind in DEADLINE_KINDS and r.at is None and r.course_id in by_course:
            targets = {
                find(d.id) for d in by_course[r.course_id] if d.source != r.source and match_strength(d.title, r.title) == 2
            }
            if len(targets) == 1:
                union(r.id, targets.pop())

    changes = [{"id": r.id, "group_id": find(r.id)} for r in rows if r.group_id != find(r.id)]
    if changes:
        s.execute(update(Item), changes)
