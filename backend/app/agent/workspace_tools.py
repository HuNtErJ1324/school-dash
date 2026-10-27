"""Helpers for the school dashboard's agent, copied into its workspace as school.py (see AGENTS.md there).

The database and library are read-only, so results go to outbox/, and the dashboard stores them when the
request finishes. Standard library only: this runs in prime-agent's own Python. Rendering PDF pages needs
PyMuPDF, which the container's own Python has, so page_image runs that one.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LIBRARY = os.path.join(HERE, "library")
OUTBOX = os.path.join(HERE, "outbox")
PAGES = os.path.join(HERE, "pages")  # rendered page images, kept for reuse
MAX_PAGE_IMAGES = 300
_PAGE = re.compile(r"^--- page (\d+) ---$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCAN = "text layer"  # in the note of documents that are scans (see the library's extract.py)


def db():
    """The read-only snapshot of the dashboard's database."""
    # immutable: the folder is mounted read-only and the dashboard only swaps in a new copy between requests.
    return sqlite3.connect(f"file:{os.path.join(DATA, 'dashboard.db')}?mode=ro&immutable=1", uri=True)


def context():
    with open(os.path.join(DATA, "context.json"), encoding="utf-8") as f:
        return json.load(f)


def _norm(course):
    return re.sub(r"[^a-z0-9]", "", str(course).lower())


def docs(course=None, kind=None):
    """The library's documents: dicts with id, course, title, kind, pages, text_path, path, url, item, note."""
    with closing(db()) as c:
        rows = c.execute(
            "SELECT d.id, d.course_id, c.code, d.title, d.kind, d.pages, d.text_path, d.path, d.url, d.item_source_id, d.note "
            "FROM documents d LEFT JOIN courses c ON c.id = d.course_id WHERE d.removed_at IS NULL ORDER BY d.course_id, d.id"
        ).fetchall()
    keys = ("id", "course_id", "course", "title", "kind", "pages", "text_path", "path", "url", "item", "note")
    out = [dict(zip(keys, r)) for r in rows]
    if course:
        want = _norm(course)
        out = [d for d in out if want in (_norm(d["course_id"] or ""), _norm(d["course"] or ""))]
    if kind:
        out = [d for d in out if d["kind"] == kind]
    return out


def search(pattern, course=None, kind=None, limit=30):
    """Case-insensitive regex search of the library's text. Returns [{doc_id, title, course, page, line_no, text}]."""
    rx = re.compile(pattern, re.I)
    out = []
    for d in docs(course, kind):
        if not d["text_path"]:
            continue
        page = None
        with open(os.path.join(LIBRARY, d["text_path"]), encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                m = _PAGE.match(line.strip())
                if m:
                    page = int(m.group(1))
                elif rx.search(line):
                    out.append({"doc_id": d["id"], "title": d["title"], "course": d["course"], "page": page,
                                "line_no": n, "text": line.strip()[:300]})
                    if len(out) >= limit:
                        return out
    return out


def _pages(spec):
    wanted = set()
    for part in str(spec).replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return wanted


def read(doc_id, pages=None, max_chars=80000):
    """A document's text, or only some pages ("12", "3-5,9"). Long text is cut at max_chars."""
    d = next((x for x in docs() if x["id"] == int(doc_id)), None)
    if d is None:
        raise ValueError(f"no document {doc_id} (see library/INDEX.md)")
    if not d["text_path"]:
        return f"Document {doc_id} has no text version ({d['note'] or 'unknown file type'}). The file is library/{d['path']}."
    with open(os.path.join(LIBRARY, d["text_path"]), encoding="utf-8", errors="replace") as f:
        text = f.read()
    if pages:
        wanted, keep, page = _pages(pages), [], None
        for line in text.splitlines(keepends=True):
            m = _PAGE.match(line.strip())
            if m:
                page = int(m.group(1))
            if page in wanted:
                keep.append(line)
        text = "".join(keep) or f"(no pages {pages} in document {doc_id}; it has {d['pages'] or 'no'} pages)"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[cut at {max_chars} characters; read fewer pages at a time]"
    if _SCAN in (d["note"] or ""):
        text = (f"[Document {doc_id} is a scan, so this text is partial or empty. Look at its pages instead: "
                f"await attach_image(school.page_image({doc_id}, page))]\n\n" + text)
    return text


_RENDER = """
import sys, pymupdf
src, page, dpi, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
with pymupdf.open(src) as doc:
    if not 1 <= page <= doc.page_count:
        sys.exit(f"page {page} is outside this document's pages 1-{doc.page_count}")
    doc[page - 1].get_pixmap(dpi=dpi).save(out)
"""
_renderer_python = None


def _renderer():
    """A Python with PyMuPDF: the container's own (this kernel's venv doesn't have it unless installed)."""
    global _renderer_python
    if _renderer_python is None:
        for py in (os.environ.get("SCHOOL_PYTHON"), "/usr/local/bin/python3", shutil.which("python3"), sys.executable):
            if py and os.path.exists(py) and subprocess.run([py, "-c", "import pymupdf"], capture_output=True).returncode == 0:
                _renderer_python = py
                break
        else:
            raise RuntimeError("no Python with PyMuPDF here to render pages")
    return _renderer_python


def page_image(doc_id, page, dpi=120):
    """Render one page of a library PDF as a PNG and return its path, to look at with
    `await attach_image(path)`. For scans (handwritten notes, image slides) and for figures, tables or
    equations the text version garbles. Higher dpi for small handwriting (60-200)."""
    d = next((x for x in docs() if x["id"] == int(doc_id)), None)
    if d is None:
        raise ValueError(f"no document {doc_id} (see library/INDEX.md)")
    if not (d["path"] or "").lower().endswith(".pdf"):
        raise ValueError(f"document {doc_id} isn't a PDF; read it with school.read({doc_id})")
    dpi = max(60, min(int(dpi), 200))
    rel = f"pages/{int(doc_id)}-p{int(page)}-{dpi}.png"
    out = os.path.join(HERE, rel)
    if not os.path.exists(out):
        os.makedirs(PAGES, exist_ok=True)
        src = os.path.join(LIBRARY, d["path"])
        p = subprocess.run([_renderer(), "-c", _RENDER, src, str(int(page)), str(dpi), out],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise ValueError((p.stderr.strip().splitlines() or ["couldn't render that page"])[-1])
        _prune_pages()
    return rel


def _prune_pages():
    images = sorted((e for e in os.scandir(PAGES) if e.name.endswith(".png")), key=lambda e: e.stat().st_mtime)
    for e in images[:-MAX_PAGE_IMAGES]:
        os.remove(e.path)


def _queue(op):
    os.makedirs(OUTBOX, exist_ok=True)
    with open(os.path.join(OUTBOX, f"{os.getpid()}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({**op, "ts": time.time()}) + "\n")


def _resources(resources):
    out = []
    for r in resources or []:
        if not isinstance(r, dict) or not str(r.get("title") or "").strip():
            raise ValueError("each resource is a dict with a title, and a doc_id (plus optional pages) or a url")
        if r.get("doc_id") is None and not str(r.get("url") or "").startswith(("http://", "https://")):
            raise ValueError(f"resource {r.get('title')!r} needs a doc_id or an http(s) url")
        out.append({"title": str(r["title"]), "doc_id": int(r["doc_id"]) if r.get("doc_id") is not None else None,
                    "pages": str(r["pages"]) if r.get("pages") else None, "url": r.get("url")})
    return out


def save_plan(date, summary, tasks, heads_up=()):
    """Save the day's plan (see AGENTS.md for the task fields). Replaces any earlier plan saved in this request."""
    if not _DATE.match(str(date)):
        raise ValueError('date must be "YYYY-MM-DD"')
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list of dicts")
    clean = []
    for t in tasks:
        if not isinstance(t, dict) or not str(t.get("title") or "").strip():
            raise ValueError("every task needs a title")
        clean.append({
            "title": str(t["title"]).strip(),
            "minutes": int(t["minutes"]) if t.get("minutes") is not None else None,
            "course": t.get("course"),
            "item_id": int(t["item_id"]) if t.get("item_id") is not None else None,
            "when": t.get("when"),
            "why": t.get("why"),
            "steps": [str(s) for s in t.get("steps") or []],
            "resources": _resources(t.get("resources")),
        })
    _queue({"op": "plan", "date": str(date), "summary": str(summary or "").strip(), "tasks": clean,
            "heads_up": [str(h) for h in heads_up or []][:6]})
    return f"Saved: a plan for {date} with {len(clean)} tasks ({sum(t['minutes'] or 0 for t in clean)} minutes)."


def save_brief(item_id, markdown, minutes=None, resources=()):
    """Save a brief for an item (its context.json id)."""
    if not str(markdown or "").strip():
        raise ValueError("the brief's markdown is empty")
    _queue({"op": "brief", "item_id": int(item_id), "markdown": str(markdown).strip(),
            "minutes": int(minutes) if minutes is not None else None, "resources": _resources(resources)})
    return f"Saved: the brief for item {item_id}."
