"""Course documents from Canvas: the syllabus, assignment descriptions, pages, and the files that
modules, pages and descriptions link to. Uses the canvas source's login, read-only."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

from ..sources.base import SourceError
from ..sources.canvas import CanvasApi
from .extract import DOCUMENT_EXTENSIONS, html_to_markdown
from .site import GOOGLE, google_export
from .store import Fetched

_FILE_LINK = re.compile(r"/files/(\d+)")


def guess_kind(name: str) -> str:
    name = name.lower()
    if "syllabus" in name:
        return "syllabus"
    if re.search(r"lecture|slides|notes", name):
        return "slides"
    if re.search(r"midterm|final|exam|practice|review", name):
        return "exam prep"
    if re.search(r"\bhw|homework|problem set|pset|assignment|worksheet|project|lab\b", name):
        return "assignment"
    return "file"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "untitled"


def assignment_text(a: dict[str, Any]) -> str:
    facts = [
        f"Due: {a['due_at']}" if a.get("due_at") else None,
        f"Points: {a['points_possible']:g}" if a.get("points_possible") is not None else None,
        "Submit: " + ", ".join(a["submission_types"]) if a.get("submission_types") else None,
    ]
    return "\n".join(f for f in facts if f) + "\n\n" + html_to_markdown(a.get("description") or "", a.get("html_url"))


async def fetch_canvas(
    api: CanvasApi,
    courses: list[tuple[str, int]],  # (dashboard course id, Canvas course id)
    known: dict[str, str | None],
    max_bytes: int,
    problems: list[str],
) -> AsyncIterator[Fetched]:
    base = api.config.base_url
    await api.get_one("/api/v1/users/self")  # fails with a clear message if the session expired
    for course_id, cid in courses:
        files: dict[int, str] = {}  # Canvas file id -> a title hint from where it was linked
        common = dict(source="canvas", course_id=course_id)

        def links(html: str | None, hint: str) -> None:
            for fid in _FILE_LINK.findall(html or ""):
                files.setdefault(int(fid), hint)

        course = await api.get_one(f"/api/v1/courses/{cid}", {"include[]": "syllabus_body"}, optional=True) or {}
        if (course.get("syllabus_body") or "").strip():
            links(course["syllabus_body"], "Syllabus")
            yield Fetched(**common, key=f"{cid}:syllabus", title="Syllabus", kind="syllabus", rel_path="syllabus.md",
                          url=urljoin(base, f"/courses/{cid}/assignments/syllabus"),
                          text=html_to_markdown(course["syllabus_body"], base))  # fmt: skip

        for a in await api.get_all(f"/api/v1/courses/{cid}/assignments", {"per_page": 100}, optional=True):
            if len((a.get("description") or "").strip()) < 40:
                continue
            links(a["description"], a.get("name") or "")
            yield Fetched(**common, key=f"{cid}:assignment:{a['id']}", title=a.get("name") or "Assignment",
                          kind="assignment", rel_path=f"assignments/{a['id']}-{_slug(a.get('name') or '')}.md",
                          url=a.get("html_url"), item_source_id=f"assignment:{a['id']}", text=assignment_text(a))  # fmt: skip

        page_urls: dict[str, str] = {}
        google: dict[tuple[str, str], str] = {}
        modules = await api.get_all(f"/api/v1/courses/{cid}/modules", {"include[]": "items", "per_page": 100}, optional=True)
        for m in modules:
            for it in m.get("items") or []:
                title = f"{m.get('name')} · {it.get('title')}" if m.get("name") else it.get("title") or ""
                if it.get("type") == "File" and it.get("content_id"):
                    files.setdefault(int(it["content_id"]), it.get("title") or title)
                elif it.get("type") == "Page" and it.get("page_url"):
                    page_urls.setdefault(it["page_url"], title)
                elif it.get("type") == "ExternalUrl" and (g := GOOGLE.match(it.get("external_url") or "")):
                    google.setdefault((g[1], g[2]), title)
        for p in await api.get_all(f"/api/v1/courses/{cid}/pages", {"per_page": 100}, optional=True):
            if p.get("url"):
                page_urls.setdefault(p["url"], p.get("title") or p["url"])

        for page_url, hint in page_urls.items():
            page = await api.get_one(f"/api/v1/courses/{cid}/pages/{page_url}", optional=True)
            if not page or page.get("locked_for_user") or not (page.get("body") or "").strip():
                continue
            links(page["body"], page.get("title") or hint)
            yield Fetched(**common, key=f"{cid}:page:{page_url}", title=page.get("title") or hint, kind="page",
                          rel_path=f"pages/{_slug(page_url)}.md", url=page.get("html_url"),
                          text=html_to_markdown(page["body"], base))  # fmt: skip

        listed = {f["id"]: f for f in await api.get_all(f"/api/v1/courses/{cid}/files", {"per_page": 100}, optional=True)}
        for fid in listed:
            files.setdefault(fid, listed[fid].get("display_name") or "")
        for fid, hint in files.items():
            meta = listed.get(fid) or await api.get_one(f"/api/v1/courses/{cid}/files/{fid}", optional=True)
            if not meta or not meta.get("url"):
                continue
            name = meta.get("display_name") or meta.get("filename") or f"file-{fid}"
            # `hidden_for_user` only keeps a file out of the Files list: instructors post notes as
            # link-only files, which the links we followed can still open. Locked ones can't be.
            if meta.get("locked_for_user"):
                problems.append(f"{name}: locked on Canvas")
                continue
            ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in DOCUMENT_EXTENSIONS:
                continue
            if (meta.get("size") or 0) > max_bytes:
                problems.append(f"{name}: larger than the {max_bytes // 2**20} MB limit")
                continue
            key, version = f"{cid}:file:{fid}", meta.get("updated_at") or meta.get("modified_at")
            doc = dict(**common, key=key, title=name if len(name) > 12 or not hint else f"{hint} ({name})",
                       kind=guess_kind(f"{name} {hint}"), rel_path=f"files/{fid}-{name}",
                       url=urljoin(base, f"/courses/{cid}/files/{fid}"), version=version)  # fmt: skip
            if version and known.get(key) == version:
                yield Fetched(**doc, unchanged=True)
                continue
            try:
                content, mime = await api.download(meta["url"], max_bytes)
            except SourceError as e:
                if "login" in str(e):
                    raise
                problems.append(f"{name}: {e}")
                continue
            yield Fetched(**doc, content=content, mime=meta.get("content-type") or mime)

        for (gkind, gid), title in google.items():
            if fetched := await google_export(api.http, gkind, gid, title, course_id, max_bytes, problems):
                yield dataclasses.replace(fetched, source="canvas")
