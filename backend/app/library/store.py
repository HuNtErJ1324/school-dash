"""The library on disk and in the database: one folder per course, the original files plus a text version.

    library/
      INDEX.md                       every document, by course (what the agent reads first)
      cse410/site/lectures/05.ink.pdf
      cse410/site/lectures/05.ink.pdf.txt
      cse410/site/pages/calendar-calendar.md
      math300/canvas/files/123-hw4.pdf ...
      math300/upload/textbook.pdf ...
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models import Course, Document
from .extract import extract


@dataclass
class Fetched:
    """One document as a fetcher found it."""

    source: str
    key: str
    title: str
    kind: str
    rel_path: str  # where it goes under <course>/<source>/, e.g. "lectures/05.pdf"
    course_id: str | None = None
    url: str | None = None
    item_source_id: str | None = None
    content: bytes | None = None  # the original file
    text: str | None = None  # or, for a web page, its text
    mime: str | None = None
    version: str | None = None
    unchanged: bool = False  # the server said it hasn't changed since `version`


def safe_rel_path(rel: str) -> str:
    parts = []
    for part in PurePosixPath(rel.replace("\\", "/")).parts:
        part = re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-.")[:120]
        if part:
            parts.append(part)
    return "/".join(parts) or "document"


class Library:
    def __init__(self, root: Path, Session: sessionmaker[Session]):
        self.root = root
        self.Session = Session
        root.mkdir(parents=True, exist_ok=True)

    def file(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"{rel!r} is outside the library")
        return path

    def known_versions(self, source: str) -> dict[str, str | None]:
        with self.Session() as s:
            return dict(s.execute(
                select(Document.key, Document.version).where(Document.source == source, Document.path.is_not(None))
            ).all())  # fmt: skip

    def save(self, f: Fetched, now: datetime | None = None) -> int:
        """Store a fetched document, re-extracting its text only if its content changed. Returns its id."""
        now = now or datetime.now(timezone.utc)
        with self.Session.begin() as s:
            doc = s.scalars(select(Document).where(Document.source == f.source, Document.key == f.key)).one_or_none()
            if doc is None:
                if f.unchanged:
                    raise ValueError(f"{f.key} was reported unchanged but was never stored")
                doc = Document(source=f.source, key=f.key, fetched_at=now, updated_at=now)
                s.add(doc)
            doc.title, doc.kind, doc.url = f.title, f.kind, f.url
            doc.course_id, doc.item_source_id = f.course_id, f.item_source_id
            doc.fetched_at, doc.removed_at = now, None
            if f.unchanged:
                s.flush()
                return doc.id

            body = f.content if f.content is not None else (f.text or "").encode()
            digest = hashlib.sha256(body).hexdigest()
            folder = f"{f.course_id or 'general'}/{f.source}"
            rel = f"{folder}/{safe_rel_path(f.rel_path)}"
            if doc.sha256 == digest and all(self.file(p).exists() for p in (doc.path, doc.text_path) if p):
                doc.version = f.version
                s.flush()
                return doc.id

            if f.content is not None:
                self._write(rel, f.content)
                got = extract(f.content, rel, f.mime)
                doc.path, doc.mime, doc.size, doc.pages, doc.note = rel, f.mime, len(f.content), got.pages, got.note
                text, text_rel = got.text, rel + ".txt"
            else:
                text, text_rel = f.text or "", rel if rel.endswith(".md") else rel + ".md"
                doc.path, doc.mime, doc.size, doc.pages, doc.note = None, "text/markdown", len(body), None, None
            if text is not None:
                header = f"# {f.title}\n\n" + (f"Source: {f.url}\n\n" if f.url else "")
                self._write(text_rel, (header + text).encode())
                doc.text_path, doc.chars = text_rel, len(text)
            else:
                doc.text_path, doc.chars = None, None
            doc.sha256, doc.version, doc.updated_at = digest, f.version, now
            s.flush()
            return doc.id

    def mark_removed(self, source: str, seen: set[str], course_ids: list[str] | None, now: datetime) -> int:
        """Documents a complete fetch didn't find anymore. Their files stay, in case they come back."""
        with self.Session.begin() as s:
            q = select(Document).where(Document.source == source, Document.removed_at.is_(None))
            if course_ids is not None:
                q = q.where(Document.course_id.in_(course_ids))
            gone = [d for d in s.scalars(q) if d.key not in seen]
            for d in gone:
                d.removed_at = now
            return len(gone)

    def delete(self, doc_id: int) -> None:
        with self.Session.begin() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                return
            for rel in (doc.path, doc.text_path):
                if rel:
                    self.file(rel).unlink(missing_ok=True)
            s.delete(doc)

    def write_index(self, tz: ZoneInfo) -> None:
        with self.Session() as s:
            courses = {c.id: c for c in s.scalars(select(Course))}
            docs = s.scalars(
                select(Document).where(Document.removed_at.is_(None)).order_by(Document.course_id, Document.kind, Document.title)
            ).all()  # fmt: skip
        stamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
        out = [
            "# Course library",
            "",
            "Every course document the dashboard has collected, by course. Paths are relative to library/. "
            "`text` is the searchable text version: PDF pages and slides are marked `--- page N ---`. "
            "`item` is the dashboard item a document describes (an items.source_id), when known.",
            "",
            f"Generated {stamp}.",
        ]
        by_course: dict[str | None, list[Document]] = {}
        for d in docs:
            by_course.setdefault(d.course_id, []).append(d)
        for course_id, group in sorted(by_course.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
            c = courses.get(course_id) if course_id else None
            name = f"{c.code}" + (f" ({c.name})" if c and c.name else "") if c else "Not tied to a course"
            out += ["", f"## {name}", "", "| id | kind | title | pages | text | item | note |", "|---|---|---|---|---|---|---|"]
            for d in group:
                cells = [str(d.id), d.kind, d.title, str(d.pages or ""), d.text_path or "", d.item_source_id or "", d.note or ""]
                out.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
        self._write("INDEX.md", ("\n".join(out) + "\n").encode())

    def _write(self, rel: str, data: bytes) -> None:
        path = self.file(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
