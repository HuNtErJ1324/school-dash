"""Canvas REST API: every assignment in your active courses with your submission and score,
plus planner items (events, announcements, ungraded discussions) for the coming weeks.

Authenticates with a personal access token if your school allows them. UW doesn't, so it
also accepts the Cookie header of a logged-in browser session (the same way Canvas's own web
pages call this API). Sessions expire; when one does, the source reports it in sync health.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, model_validator

from ..coursecode import parse_course_code
from ..quarters import code_from_term
from .base import CourseRef, FetchResult, ItemIn, Kind, Source, SourceContext, SourceError, Status, register
from .classify import kind_from_title

_KINDS: dict[str, Kind] = {
    "assignment": "assignment",
    "quiz": "quiz",
    "discussion_topic": "discussion",
    "announcement": "announcement",
    "calendar_event": "event",
}
_DEADLINE_TYPES = {"assignment", "quiz", "discussion_topic"}
_MAX_PAGES = 50


class CanvasConfig(BaseModel):
    base_url: str = "https://canvas.uw.edu"
    token: str | None = None  # personal access token
    cookie: str | None = None  # or: the Cookie request header from a logged-in browser tab
    lookback_days: float = 14
    lookahead_days: float = 150
    # Canvas term names to include, e.g. ["Autumn 2026"]. By default: the term most of your
    # active courses share, which leaves out year-long trainings and orientation "courses".
    terms: list[str] | None = None

    @model_validator(mode="after")
    def _one_auth_method(self) -> CanvasConfig:
        if bool(self.token) == bool(self.cookie):
            raise ValueError("set exactly one of token or cookie")
        return self


@register
class CanvasSource(Source[CanvasConfig]):
    type = "canvas"
    Config = CanvasConfig
    default_interval = timedelta(minutes=15)

    async def fetch(self, ctx: SourceContext) -> FetchResult:
        base = self.config.base_url
        api = CanvasApi(self.config, ctx.http)
        start, end = ctx.window(self.config.lookback_days, self.config.lookahead_days)
        active = await api.get_all("/api/v1/courses", {"enrollment_state": "active", "per_page": 100, "include[]": "term"})
        courses = current_term(active, self.config.terms)
        kept = {c["id"] for c in courses}
        planner = await api.get_all("/api/v1/planner/items", {
            "start_date": start.isoformat(), "end_date": end.isoformat(), "per_page": 100,
        })  # fmt: skip
        # Graded work comes from each course's full assignment list (it has scores and isn't
        # limited to the planner's window), so leave it out of the planner's items.
        items = [
            planner_item(p, base) for p in planner
            if not _is_graded_work(p) and (p.get("course_id") is None or p["course_id"] in kept)
        ]  # fmt: skip
        for c in courses:
            if c.get("access_restricted_by_date"):
                continue
            groups = await api.get_all(f"/api/v1/courses/{c['id']}/assignment_groups", {"per_page": 100})
            group_names = {g["id"]: g.get("name") for g in groups}
            assignments = await api.get_all(f"/api/v1/courses/{c['id']}/assignments", {"include[]": "submission", "per_page": 100})
            items += [assignment_item(a, c["id"], group_names, base) for a in assignments]
        return FetchResult(items=items, courses=[course_ref(c, base) for c in courses], window=(start, end))


class CanvasApi:
    """GETs against the Canvas API with a token or a browser session cookie. Read-only."""

    def __init__(self, config: CanvasConfig, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.host = urlsplit(config.base_url).netloc

    def _headers(self) -> dict[str, str]:
        if self.config.token:
            return {"Accept": "application/json", "Authorization": f"Bearer {self.config.token}"}
        return {"Accept": "application/json", "Cookie": self.config.cookie or ""}

    async def _get(self, url: str, params: dict[str, Any] | None, path: str, optional: bool) -> httpx.Response | None:
        try:
            resp = await self.http.get(url, params=params, headers=self._headers(), follow_redirects=False)
        except httpx.HTTPError as e:
            raise SourceError(f"couldn't reach Canvas ({type(e).__name__})") from e
        # A course can turn off Files or Pages for students, which Canvas answers with 401/403/404,
        # so `optional` reads treat those as "not available" (check the login with a required read first).
        if optional and resp.status_code in (401, 403, 404):
            return None
        if resp.status_code in (401, 403) or resp.is_redirect:
            fix = "paste a fresh session cookie into .env" if self.config.cookie else "check the API token"
            raise SourceError(f"Canvas rejected the login; {fix}")
        if not resp.is_success:
            raise SourceError(f"Canvas {path} returned HTTP {resp.status_code}")
        return resp

    async def get_one(self, path: str, params: dict[str, Any] | None = None, optional: bool = False) -> Any:
        resp = await self._get(urljoin(self.config.base_url, path), params, path, optional)
        return _json(resp.text) if resp is not None else None

    async def get_all(self, path: str, params: dict[str, Any], optional: bool = False) -> list[dict[str, Any]]:
        url: str | None = urljoin(self.config.base_url, path)
        query: dict[str, Any] | None = params
        out: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            if not url:
                return out
            resp = await self._get(url, query, path, optional)
            if resp is None:
                return out
            out.extend(_json(resp.text))
            # The next link already carries the query. (params={} would replace it, not keep it.)
            url, query = resp.links.get("next", {}).get("url"), None
        raise SourceError(f"Canvas {path} kept paginating past {_MAX_PAGES} pages")

    async def download(self, url: str, max_bytes: int) -> tuple[bytes, str | None]:
        """A file's bytes. Canvas redirects downloads to its file store; the login goes to Canvas only."""
        for _ in range(6):
            same_host = urlsplit(url).netloc == self.host
            headers = self._headers() if same_host else {}
            try:
                resp = await self.http.get(url, headers=headers, follow_redirects=False)
            except httpx.HTTPError as e:
                raise SourceError(f"couldn't download a Canvas file ({type(e).__name__})") from e
            if resp.is_redirect:
                url = urljoin(url, resp.headers["location"])
                if urlsplit(url).netloc == self.host and urlsplit(url).path.startswith("/login"):
                    raise SourceError("Canvas rejected the login; paste a fresh session cookie into .env")
                continue
            if not resp.is_success:
                raise SourceError(f"Canvas file download returned HTTP {resp.status_code}")
            if len(resp.content) > max_bytes:
                raise SourceError(f"larger than the {max_bytes // 2**20} MB limit")
            return resp.content, resp.headers.get("content-type")
        raise SourceError("Canvas file download redirected too many times")


def _json(text: str) -> Any:
    # Canvas prefixes cookie-authenticated JSON with while(1); to block JSON hijacking.
    return json.loads(text.removeprefix("while(1);"))


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def course_ref(c: dict[str, Any], base_url: str) -> CourseRef:
    return CourseRef(
        canvas_id=c["id"],
        code=parse_course_code(c.get("course_code")) or c.get("course_code"),
        name=c.get("name"),
        links={"canvas": urljoin(base_url, f"/courses/{c['id']}")},
        term=code_from_term((c.get("term") or {}).get("name")),
    )


def submission_status(subs: Any) -> Status | None:
    if not isinstance(subs, dict):  # False when there's nothing to submit
        return None
    if subs.get("excused"):
        return "excused"
    if subs.get("graded"):
        return "graded"
    if subs.get("submitted"):
        return "submitted"
    if subs.get("missing"):
        return "missing"
    return "pending"


_SEASON = re.compile(r"\b(autumn|fall|winter|spring|summer)\b", re.I)


def current_term(courses: list[dict[str, Any]], terms: list[str] | None) -> list[dict[str, Any]]:
    """Your classes this term, without trainings that sit in terms of their own.

    Canvas's term dates aren't reliable (UW's Autumn 2026 "starts" in 2025), so a tie between
    terms goes to the one named like an academic term ("Autumn 2026") over "2025-2026 101".
    """
    def name(c: dict[str, Any]) -> str | None:
        return (c.get("term") or {}).get("name")

    courses = [c for c in courses if "id" in c]
    if terms:
        return [c for c in courses if name(c) in terms]
    counts = Counter(name(c) for c in courses if name(c))
    if not counts:
        return courses
    main = max(counts, key=lambda t: (counts[t], bool(_SEASON.search(t))))
    return [c for c in courses if name(c) == main]


def _is_graded_work(p: dict[str, Any]) -> bool:
    """Planner entries that are also in a course's assignment list."""
    ptype = p.get("plannable_type")
    if ptype in ("assignment", "quiz"):
        return True
    return ptype == "discussion_topic" and bool((p.get("plannable") or {}).get("assignment_id"))


def assignment_status(a: dict[str, Any]) -> Status | None:
    sub = a.get("submission") or {}
    if sub.get("excused"):
        return "excused"
    if sub.get("workflow_state") == "graded" and sub.get("score") is not None:
        return "graded"
    if sub.get("submitted_at") or sub.get("workflow_state") in ("submitted", "pending_review"):
        return "submitted"
    if sub.get("missing"):
        return "missing"
    if set(a.get("submission_types") or []) & {"none", "on_paper", "not_graded"}:
        return None  # nothing to hand in through Canvas
    return "pending"


def assignment_item(a: dict[str, Any], course_id: int, group_names: dict[int, str | None], base_url: str) -> ItemIn:
    title = a.get("name") or "(untitled)"
    types = set(a.get("submission_types") or [])
    if a.get("is_quiz_assignment") or a.get("quiz_id") or "online_quiz" in types:
        kind: Kind = kind_from_title(title, default="quiz")
    elif "discussion_topic" in types:
        kind = "discussion"
    else:
        kind = kind_from_title(title)
    due = _dt(a.get("due_at"))
    sub = a.get("submission") or {}
    return ItemIn(
        # Same id scheme as canvas_feed, so the two recognise each other's items.
        source_id=f"assignment:{a['id']}",
        kind=kind,
        title=title,
        url=a.get("html_url") or urljoin(base_url, f"/courses/{course_id}/assignments/{a['id']}"),
        course=CourseRef(canvas_id=course_id),
        due_at=due,
        starts_at=due if kind == "exam" else None,
        status=assignment_status(a),
        points=a.get("points_possible"),
        score=sub.get("score") if sub.get("workflow_state") == "graded" else None,
        category=group_names.get(a.get("assignment_group_id")),
        raw={k: a.get(k) for k in ("id", "name", "due_at", "lock_at", "submission_types", "assignment_group_id")}
        | {"submission": {k: sub.get(k) for k in ("workflow_state", "submitted_at", "late", "missing", "excused")}},
    )


def planner_item(p: dict[str, Any], base_url: str) -> ItemIn:
    ptype = p.get("plannable_type", "")
    pl = p.get("plannable") or {}
    when = _dt(p.get("plannable_date"))
    title = pl.get("title") or pl.get("name") or "(untitled)"
    kind = kind_from_title(title) if ptype == "assignment" else _KINDS.get(ptype, "other")
    common: dict[str, Any] = dict(
        source_id=f"{ptype}:{p.get('plannable_id')}",
        kind=kind,
        title=title,
        url=urljoin(base_url, p["html_url"]) if p.get("html_url") else None,
        course=CourseRef(canvas_id=p["course_id"], name=p.get("context_name")) if p.get("course_id") else None,
        status=submission_status(p.get("submissions")),
        points=pl.get("points_possible"),
        raw=p,
    )
    if ptype in _DEADLINE_TYPES:
        due = _dt(pl.get("due_at")) or when
        return ItemIn(**common, due_at=due, starts_at=due if kind == "exam" else None)
    if ptype == "calendar_event":
        return ItemIn(
            **common,
            starts_at=_dt(pl.get("start_at")) or when,
            ends_at=_dt(pl.get("end_at")),
            all_day=bool(pl.get("all_day")),
            location=pl.get("location_name"),
        )
    return ItemIn(**common, starts_at=when)
