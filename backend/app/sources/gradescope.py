"""Gradescope, read from the same pages you see (it has no public API).

UW logs into Gradescope through NetID single sign-on, which a script can't do, so this uses
the Cookie header of a logged-in browser tab. Sessions expire; when one does, sync health
says so and you paste a fresh one. It reads your course list, then one page per course,
pausing between pages.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from pydantic import BaseModel

from ..coursecode import parse_course_code
from ..quarters import code_from_term
from .base import CourseRef, FetchResult, ItemIn, Source, SourceContext, SourceError, Status, register
from .classify import kind_from_title
from .ics import slug

# "55.75 / 61.0", sometimes followed by lateness: "55.75 / 61.0 1 Day, 3 Minutes Late".
_SCORE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\b")
_COURSE_HREF = re.compile(r"/courses/(\d+)/?$")
_ASSIGNMENT_HREF = re.compile(r"/assignments/(\d+)")


class GradescopeConfig(BaseModel):
    cookie: str  # the Cookie request header from a logged-in gradescope.com tab
    base_url: str = "https://www.gradescope.com"
    all_terms: bool = False  # by default, only the newest term's courses
    course_map: dict[int, str] = {}  # Gradescope course id -> course code, when the short name isn't one
    pause_seconds: float = 1.0  # between course pages


@dataclass
class GsCourse:
    id: int
    short_name: str
    full_name: str
    term: str


@dataclass
class GsAssignment:
    id: str | None
    name: str
    status_text: str
    href: str | None
    release: datetime | None
    due: datetime | None
    late_due: datetime | None


@register
class GradescopeSource(Source[GradescopeConfig]):
    type = "gradescope"
    Config = GradescopeConfig
    default_interval = timedelta(hours=1)

    async def fetch(self, ctx: SourceContext) -> FetchResult:
        cfg = self.config
        courses = parse_courses(await self._get(ctx, "/account"), all_terms=cfg.all_terms)
        if courses is None:
            raise SourceError("Gradescope didn't show your courses; the session has probably expired. Paste a fresh cookie into .env")

        items: list[ItemIn] = []
        refs: list[CourseRef] = []
        for n, course in enumerate(courses):
            if n:
                await asyncio.sleep(cfg.pause_seconds)
            ref = CourseRef(
                code=cfg.course_map.get(course.id) or parse_course_code(course.short_name) or course.short_name,
                name=course.full_name or None,
                links={"gradescope": f"{cfg.base_url}/courses/{course.id}"},
                term=code_from_term(course.term),
            )
            refs.append(ref)
            page = await self._get(ctx, f"/courses/{course.id}")
            items += [to_item(a, course.id, ref, cfg.base_url, ctx.now()) for a in parse_assignments(page)]
        # Course pages list every assignment, so no window: anything gone from them is gone.
        return FetchResult(items=items, courses=refs)

    async def _get(self, ctx: SourceContext, path: str) -> str:
        try:
            resp = await ctx.http.get(
                self.config.base_url + path,
                headers={"Cookie": self.config.cookie, "Accept": "text/html"},
                follow_redirects=False,
            )
        except httpx.HTTPError as e:
            raise SourceError(f"couldn't reach Gradescope ({type(e).__name__})") from e
        if resp.status_code in (401, 403) or resp.is_redirect:
            raise SourceError("Gradescope rejected the session; paste a fresh cookie into .env")
        if resp.status_code != 200:
            raise SourceError(f"Gradescope {path} returned HTTP {resp.status_code}")
        return resp.text


def parse_courses(html: str, all_terms: bool = False) -> list[GsCourse] | None:
    """Your student courses from the account page, newest term first. None if not logged in."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#account-show")
    if root is None:
        return None
    # Staff who are also students get "Instructor Courses" and "Student Courses" sections.
    student = True
    courses: list[GsCourse] = []
    for el in root.find_all(True):
        classes = el.get("class") or []
        if "pageHeading" in classes:
            student = el.get_text(strip=True) != "Instructor Courses"
        elif student and "courseList--term" in classes:
            term = el.get_text(" ", strip=True)
            for box in _boxes_after(el):
                m = _COURSE_HREF.search(box["href"])
                if not m:
                    continue  # e.g. the "add a course" box
                short = box.select_one(".courseBox--shortname")
                full = box.select_one(".courseBox--name")
                courses.append(GsCourse(
                    id=int(m.group(1)),
                    short_name=short.get_text(" ", strip=True) if short else "",
                    full_name=full.get_text(" ", strip=True) if full else "",
                    term=term,
                ))  # fmt: skip
    if courses and not all_terms:
        courses = [c for c in courses if c.term == courses[0].term]
    return courses


def _boxes_after(term_heading) -> list:
    """Course boxes between a term heading and the next one, nested in a wrapper or not."""
    boxes = []
    for sib in term_heading.next_siblings:
        if not hasattr(sib, "get"):
            continue  # text between tags
        if "courseList--term" in (sib.get("class") or []):
            break
        if sib.name == "a" and "courseBox" in (sib.get("class") or []) and sib.get("href"):
            boxes.append(sib)
        else:
            boxes += sib.select("a.courseBox[href]")
    return boxes


def parse_assignments(html: str) -> list[GsAssignment]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#assignments-student-table") or soup
    out = []
    for row in table.select("tr"):
        head, cells = row.find("th"), row.find_all("td")
        if head is None or not cells:
            continue  # the header row (all th) and the upload dropzone row (no th)
        link = head.find("a", href=True)
        button = head.find("button", attrs={"data-assignment-id": True})
        aid = None
        if link and (m := _ASSIGNMENT_HREF.search(link["href"])):
            aid = m.group(1)
        elif button:
            aid = str(button["data-assignment-id"])
        dues = row.select(".submissionTimeChart--dueDate")
        out.append(GsAssignment(
            id=aid,
            name=" ".join(head.get_text(" ", strip=True).split()),
            status_text=" ".join(cells[0].get_text(" ", strip=True).split()),
            href=link["href"] if link else None,
            release=_when(row.select_one(".submissionTimeChart--releaseDate")),
            due=_when(dues[0]) if dues else None,
            late_due=_when(dues[1]) if len(dues) > 1 else None,
        ))  # fmt: skip
    return out


def _when(el) -> datetime | None:
    value = el.get("datetime") if el is not None else None
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        parsed = dateparser.parse(value)
        return parsed if parsed.tzinfo else None


def to_item(a: GsAssignment, course_id: int, course: CourseRef, base_url: str, now: datetime) -> ItemIn:
    kind = kind_from_title(a.name)
    score = points = None
    if m := _SCORE.match(a.status_text):
        score, points = float(m.group(1)), float(m.group(2))

    status: Status | None
    if score is not None:
        status = "graded"
    elif "submitted" in a.status_text.lower() and "no submission" not in a.status_text.lower():
        status = "submitted"
    elif a.due is None or kind == "exam":
        status = None  # nothing for you to hand in, or no deadline to miss
    else:
        status = "missing" if now > (a.late_due or a.due) else "pending"

    # A submitted assignment links to its submission. An unsubmitted one is either a
    # button or a link to ".../submissions/new" (the upload form): link to the assignment.
    if a.href and "/submissions/" in a.href and not a.href.rstrip("/").endswith("/new"):
        url = base_url + a.href if a.href.startswith("/") else a.href
    elif a.id:
        url = f"{base_url}/courses/{course_id}/assignments/{a.id}"
    else:
        url = f"{base_url}/courses/{course_id}"

    return ItemIn(
        source_id=f"gradescope:{a.id}" if a.id else f"gradescope:{course_id}:{slug(a.name)}",
        kind=kind,
        title=a.name,
        url=url,
        course=course,
        due_at=a.due,
        status=status,
        points=points,
        score=score,
        raw={
            "status": a.status_text,
            "late": "late" in a.status_text.lower(),
            "release": a.release.isoformat() if a.release else None,
            "late_due": a.late_due.isoformat() if a.late_due else None,
            "gradescope_course": course_id,
        },
    )
