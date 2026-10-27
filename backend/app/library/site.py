"""A course website: its pages as text, the documents they link (slides, specs, manuals), and the
Google Slides and Docs they link, exported as PDF when they're shared publicly."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from ..config import SiteConfig
from .extract import DOCUMENT_EXTENSIONS, html_to_markdown
from .store import Fetched

_GENERIC = {"slides", "slide", "pdf", "ink", "inked", "link", "here", "notes", "handout", "pptx", "download", "view"}
GOOGLE = re.compile(r"https://docs\.google\.com/(presentation|document)/d/([A-Za-z0-9_-]{20,})")
_PAGE_EXTENSIONS = {"", ".html", ".htm"}
_MAX_DEPTH = 3


def _ext(url: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _clean(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def link_title(a: Tag) -> str:
    """What a link is: its text, or for "Slides"/"ink" links, the calendar event it sits in."""
    text = " ".join(a.get_text(" ", strip=True).split())
    if len(text) > 3 and text.lower() not in _GENERIC:
        return text[:150]
    parts: list[str] = []
    box = a.find_parent(lambda t: t.name in ("div", "li", "tr", "section") and t.find(class_="summary") is not None)
    if box is not None:
        for cls in ("summary", "description"):
            el = box.find(class_=cls)
            if el is not None:
                parts.append(" ".join(el.get_text(" ", strip=True).split()))
        day = a.find_parent(id=re.compile(r"^\d{4}-\d{2}-\d{2}$"))
        if day is not None:
            d = datetime.strptime(day["id"], "%Y-%m-%d")
            parts.append(f"{d:%b} {d.day}")
        reading = box.find(class_="reading")
        if reading is not None:
            parts.append("reading: " + " ".join(reading.get_text(" ", strip=True).split()))
    else:
        parent = a.find_parent(["li", "p", "td", "h1", "h2", "h3", "h4"])
        if parent is not None:
            parts.append(" ".join(parent.get_text(" ", strip=True).split())[:100])
    title = " · ".join(p for p in parts if p) or urlsplit(a.get("href", "")).path.rsplit("/", 1)[-1] or text
    return title + (" (inked)" if text.lower() in ("ink", "inked") else "")


def file_kind(rel: str) -> str:
    rel = rel.lower()
    if re.search(r"lecture|slides|section", rel):
        return "slides"
    if re.search(r"resource|manual|reference", rel):
        return "reference"
    if re.search(r"reading", rel):
        return "reading"
    if re.search(r"lab|pset|hw|homework|assignment|project", rel):
        return "assignment"
    return "file"


def prefer_inked(files: dict[str, str]) -> dict[str, str]:
    """Lecture slides come plain and annotated in class ("05.pdf", "05.ink.pdf"): keep the annotated one."""
    return {u: t for u, t in files.items() if not (u.endswith(".pdf") and u[:-4] + ".ink.pdf" in files)}


async def crawl_site(
    http: httpx.AsyncClient,
    site: SiteConfig,
    course_id: str | None,
    known: dict[str, str | None],
    max_bytes: int,
    problems: list[str],
    pause: float = 0.1,
) -> AsyncIterator[Fetched]:
    root = site.url if site.url.endswith("/") else site.url + "/"
    skip = [re.compile(p) for p in site.skip]
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    queued, fetched_pages = {root}, set()
    files: dict[str, str] = {}
    google: dict[tuple[str, str], str] = {}

    while queue and len(fetched_pages) < site.max_pages:
        url, depth = queue.popleft()
        try:
            resp = await http.get(url)
        except httpx.HTTPError as e:
            problems.append(f"{url}: {type(e).__name__}")
            continue
        final = _clean(str(resp.url))
        if not resp.is_success or "html" not in resp.headers.get("content-type", "") or final in fetched_pages:
            continue
        fetched_pages.add(final)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = _clean(urljoin(final, a["href"]))
            if any(p.search(href) for p in skip):
                continue
            if g := GOOGLE.match(href):
                google.setdefault((g[1], g[2]), link_title(a))
                continue
            if not href.startswith(root):
                continue
            ext = _ext(href)
            if ext in _PAGE_EXTENSIONS:
                if depth < _MAX_DEPTH and href not in queued:
                    queued.add(href)
                    queue.append((href, depth + 1))
            elif ext in DOCUMENT_EXTENSIONS:
                files.setdefault(href, link_title(a))
        title = soup.title.get_text(" ", strip=True) if soup.title else final.removeprefix(root) or "Home"
        rel = final.removeprefix(root).strip("/").removesuffix(".html").removesuffix(".htm") or "index"
        text = html_to_markdown("", final, soup=soup)
        yield Fetched(source="site", key=final, title=title, kind="page", rel_path=f"pages/{rel.replace('/', '-')}.md",
                      course_id=course_id, url=final, text=text)  # fmt: skip
        await asyncio.sleep(pause)

    for url, title in prefer_inked(files).items():
        rel = url.removeprefix(root)
        common = dict(source="site", key=url, title=title, kind=file_kind(rel), rel_path=rel, course_id=course_id, url=url)
        headers = {}
        version = known.get(url) or ""
        if version.startswith("etag:"):
            headers["If-None-Match"] = version[5:]
        elif version.startswith("lm:"):
            headers["If-Modified-Since"] = version[3:]
        try:
            resp = await http.get(url, headers=headers)
        except httpx.HTTPError as e:
            problems.append(f"{rel}: {type(e).__name__}")
            continue
        if resp.status_code == 304:
            yield Fetched(**common, version=version, unchanged=True)
        elif not resp.is_success:
            problems.append(f"{rel}: HTTP {resp.status_code}")
        elif len(resp.content) > max_bytes:
            problems.append(f"{rel}: larger than the {max_bytes // 2**20} MB limit")
        else:
            etag, modified = resp.headers.get("etag"), resp.headers.get("last-modified")
            yield Fetched(**common, content=resp.content, mime=resp.headers.get("content-type"),
                          version=f"etag:{etag}" if etag else f"lm:{modified}" if modified else None)  # fmt: skip
        await asyncio.sleep(pause)

    for (gkind, gid), title in google.items():
        if fetched := await google_export(http, gkind, gid, title, course_id, max_bytes, problems):
            yield fetched


async def google_export(
    http: httpx.AsyncClient, gkind: str, gid: str, title: str, course_id: str | None, max_bytes: int, problems: list[str]
) -> Fetched | None:
    """A Google Slides deck or Doc as PDF. Works only for files shared with anyone who has the link."""
    view = f"https://docs.google.com/{gkind}/d/{gid}"
    export = f"{view}/export/pdf" if gkind == "presentation" else f"{view}/export?format=pdf"
    try:
        resp = await http.get(export)
    except httpx.HTTPError as e:
        problems.append(f"Google {gkind} {gid[:8]}…: {type(e).__name__}")
        return None
    if not resp.is_success or not resp.content.startswith(b"%PDF-"):
        problems.append(f"{title}: Google {gkind} isn't shared publicly (HTTP {resp.status_code})")
        return None
    if len(resp.content) > max_bytes:
        problems.append(f"{title}: larger than the {max_bytes // 2**20} MB limit")
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or gkind
    return Fetched(source="site", key=f"google:{gid}", title=title, kind="slides" if gkind == "presentation" else "page",
                   rel_path=f"google/{slug}-{gid[:8]}.pdf", course_id=course_id, url=view, content=resp.content,
                   mime="application/pdf")  # fmt: skip
