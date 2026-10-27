from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings, SiteConfig
from app.db import make_engine, make_sessionmaker
from app.library import Fetched, Library
from app.library.canvas_docs import fetch_canvas
from app.library.extract import extract, html_to_markdown
from app.library.site import crawl_site
from app.main import create_app
from app.migrate import upgrade
from app.models import Course, Document
from app.sources.base import SourceError
from app.sources.canvas import CanvasApi, CanvasConfig

ROOT = "https://courses.example.edu/cse410/26au/"


def pdf(*pages: str) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def office(kind: str, paragraphs: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if kind == "docx":
            body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
            z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                       f"<w:body>{body}</w:body></w:document>")  # fmt: skip
        else:
            for i, p in enumerate(paragraphs, 1):
                z.writestr(f"ppt/slides/slide{i}.xml", '<p:sld xmlns:p="x" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                           f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p></p:sld>")  # fmt: skip
    return buf.getvalue()


def test_text_from_each_kind_of_document():
    got = extract(pdf("Page tables map virtual pages", "TLB shootdowns"), "05.pdf")
    assert got.pages == 2
    assert got.text.index("--- page 1 ---") < got.text.index("Page tables") < got.text.index("--- page 2 ---") < got.text.index("TLB")

    assert extract(office("docx", ["Homework 4", "Prove the lemma"]), "hw4.docx").text == "Homework 4\n\nProve the lemma"
    slides = extract(office("pptx", ["Intro", "Locks"]), "talk.pptx")
    assert slides.pages == 2 and "--- page 2 ---\n\nLocks" in slides.text
    assert extract(b"int main() {}", "lab.c").text == "int main() {}"
    assert extract(b"\x89PNG...", "diagram.png").text is None


def test_scanned_pdfs_say_so():
    got = extract(pdf("", ""), "scan.pdf")
    assert got.note and "scanned" in got.note


def test_html_keeps_the_content_and_drops_the_chrome():
    md = html_to_markdown(
        "<html><body><nav>Home Calendar</nav><main><h2>Lab 3</h2><p>Read <a href='spec.html'>the spec</a>.</p>"
        "<script>x()</script></main><footer>UW</footer></body></html>", ROOT + "labs/"
    )  # fmt: skip
    assert md.startswith("## Lab 3")
    assert f"[the spec]({ROOT}labs/spec.html)" in md
    assert "Home Calendar" not in md and "UW" not in md and "x()" not in md


CALENDAR = """<html><head><title>CSE 410: Calendar</title></head><body><main>
<a href="../labs/">Labs</a> <a href="https://elsewhere.edu/page">elsewhere</a>
<table><tr><td class='eventtd' id='2026-10-09'>
<div class='lecture'>11:30-12:20 <span class='summary'>Lecture</span><span class='description'>Processes</span>
<span class='reading'>OSPP Ch 3.1-3.3</span>
<span class='materials'><a href="../lectures/05.pdf">Slides</a> (<a href="../lectures/05.ink.pdf">ink</a>)</span></div>
<div class='section'><span class='summary'>Section</span><span class='description'>Lab 1</span>
<a href="https://docs.google.com/presentation/d/1DemoSlidesIdOnlyForTests0000000/edit?usp=sharing">Slides</a></div>
</td></tr></table></main></body></html>"""


def site_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        html = {"Content-Type": "text/html"}
        if url == ROOT:
            return httpx.Response(200, headers=html, text='<title>CSE 410</title><a href="calendar/calendar.html">Calendar</a> <a href="resources/">Resources</a>')
        if url == ROOT + "calendar/calendar.html":
            return httpx.Response(200, headers=html, text=CALENDAR)
        if url == ROOT + "labs/":
            return httpx.Response(200, headers=html, text="<title>CSE 410: Labs</title><main>You will be completing the xk project.</main>")
        if url == ROOT + "resources/":
            return httpx.Response(200, headers=html, text='<title>Resources</title><a href="elf.pdf">ELF format</a>')
        if url == ROOT + "resources/elf.pdf":
            if request.headers.get("if-none-match") == '"elf-v1"':
                return httpx.Response(304)
            return httpx.Response(200, headers={"ETag": '"elf-v1"', "Content-Type": "application/pdf"}, content=pdf("ELF header"))
        if url.startswith(ROOT + "lectures/05"):
            return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=pdf("Processes, " + url[-8:]))
        if url.startswith("https://docs.google.com/presentation/d/1DemoSlidesIdOnlyForTests0000000/export/pdf"):
            return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=pdf("Section 1: lab 1"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def crawl(known: dict[str, str | None] | None = None) -> tuple[list[Fetched], list[httpx.Request], list[str]]:
    seen: list[httpx.Request] = []
    problems: list[str] = []
    site = SiteConfig(course="CSE 410", url=ROOT)
    async with httpx.AsyncClient(transport=site_transport(seen), follow_redirects=True) as http:
        got = [f async for f in crawl_site(http, site, "cse410", known or {}, 2**20, problems, pause=0)]
    return got, seen, problems


@pytest.mark.anyio
async def test_crawl_reads_pages_documents_and_shared_slides():
    got, seen, problems = await crawl()
    by_key = {f.key: f for f in got}
    assert {f.title for f in got if f.kind == "page"} == {"CSE 410", "CSE 410: Calendar", "CSE 410: Labs", "Resources"}
    assert not any("elsewhere" in str(r.url) for r in seen)  # other sites aren't crawled

    # The annotated lecture slides win over the plain ones, titled from the calendar entry.
    assert ROOT + "lectures/05.pdf" not in by_key
    lecture = by_key[ROOT + "lectures/05.ink.pdf"]
    assert lecture.title == "Lecture · Processes · Oct 9 · reading: OSPP Ch 3.1-3.3 (inked)"
    assert (lecture.kind, lecture.rel_path) == ("slides", "lectures/05.ink.pdf")

    elf = by_key[ROOT + "resources/elf.pdf"]
    assert (elf.title, elf.kind, elf.version) == ("ELF format", "reference", 'etag:"elf-v1"')

    slides = by_key["google:1DemoSlidesIdOnlyForTests0000000"]
    assert slides.title == "Section · Lab 1 · Oct 9" and slides.content.startswith(b"%PDF-")
    assert problems == []


@pytest.mark.anyio
async def test_crawl_skips_unchanged_files():
    got, _, _ = await crawl(known={ROOT + "resources/elf.pdf": 'etag:"elf-v1"'})
    elf = next(f for f in got if f.key == ROOT + "resources/elf.pdf")
    assert elf.unchanged and elf.content is None


@pytest.fixture
def library(tmp_path):
    engine = make_engine(tmp_path / "db.sqlite")
    upgrade(engine)
    Session = make_sessionmaker(engine)
    with Session.begin() as s:
        s.add(Course(id="cse410", code="CSE 410", name="Operating Systems", links={}))
    return Library(tmp_path / "library", Session)


@pytest.mark.anyio
async def test_library_stores_files_and_text_and_tracks_changes(library):
    got, _, _ = await crawl()
    t0 = datetime(2026, 10, 24, tzinfo=timezone.utc)
    ids = {f.key: library.save(f, t0) for f in got}
    with library.Session() as s:
        lecture = s.get(Document, ids[ROOT + "lectures/05.ink.pdf"])
        calendar = s.get(Document, ids[ROOT + "calendar/calendar.html"])
    assert lecture.path == "cse410/site/lectures/05.ink.pdf" and lecture.pages == 1
    assert library.file(lecture.path).read_bytes().startswith(b"%PDF-")
    text = library.file(lecture.text_path).read_text(encoding="utf-8")
    assert text.startswith("# Lecture · Processes") and "--- page 1 ---" in text
    assert calendar.path is None and calendar.text_path == "cse410/site/pages/calendar-calendar.md"

    # Unchanged on the next sync: nothing rewritten. Gone from the site: marked removed.
    again, _, _ = await crawl(known=library.known_versions("site"))
    t1 = datetime(2026, 10, 25, tzinfo=timezone.utc)
    for f in again:
        if "labs" not in f.key:
            library.save(f, t1)
    with library.Session() as s:
        elf = s.scalars(select(Document).where(Document.key == ROOT + "resources/elf.pdf")).one()
        assert (elf.fetched_at, elf.updated_at) == (t1, t0)
    assert library.mark_removed("site", {f.key for f in again if "labs" not in f.key}, ["cse410"], t1) == 1

    library.write_index(timezone.utc)
    index = library.file("INDEX.md").read_text(encoding="utf-8")
    assert "## CSE 410 (Operating Systems)" in index and "cse410/site/lectures/05.ink.pdf.txt" in index
    assert "CSE 410: Labs" not in index


def test_library_paths_stay_inside_the_library(library):
    doc = Fetched(source="upload", key="x", title="Notes", kind="notes", rel_path="../../outside/notes.md", text="hi")
    library.save(doc)
    with library.Session() as s:
        stored = s.scalars(select(Document)).one()
    assert stored.text_path == "general/upload/outside/notes.md"
    with pytest.raises(ValueError):
        library.file("../outside.txt")


CANVAS = "https://canvas.example.edu"


def canvas_transport(seen: list[httpx.Request], expired: bool = False) -> httpx.MockTransport:
    def page(data: object) -> httpx.Response:
        return httpx.Response(200, text="while(1);" + json.dumps(data), headers={"Content-Type": "application/json"})

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.url.host == "files.example.net":
            return httpx.Response(200, content=pdf("Homework 4: Chebyshev"), headers={"Content-Type": "application/pdf"})
        if expired:
            return httpx.Response(401)
        if path == "/api/v1/users/self":
            return page({"id": 1})
        if path == "/api/v1/courses/77":
            return page({"id": 77, "syllabus_body": '<p>Grading: homework 30%. <a href="/courses/77/files/902">Answers</a></p>'})
        if path == "/api/v1/courses/77/assignments":
            return page([
                {"id": 5, "name": "HW4", "due_at": "2026-10-30T06:59:00Z", "points_possible": 10,
                 "submission_types": ["online_upload"], "html_url": f"{CANVAS}/courses/77/assignments/5",
                 "description": f'<p>Do the problems in <a href="{CANVAS}/courses/77/files/900/download">hw4.pdf</a>.</p>'},
                {"id": 6, "name": "Quiz 3", "description": ""},
            ])  # fmt: skip
        if path == "/api/v1/courses/77/modules":
            return page([{"name": "Week 4", "items": [{"type": "Page", "title": "Week 4 notes", "page_url": "week-4"},
                                                      {"type": "File", "title": "Slides 4", "content_id": 901}]}])  # fmt: skip
        if path in ("/api/v1/courses/77/pages", "/api/v1/courses/77/files"):
            return httpx.Response(401)  # tabs hidden from students
        if path == "/api/v1/courses/77/pages/week-4":
            return page({"title": "Week 4 notes", "body": "<p>Read 2.3</p>", "html_url": f"{CANVAS}/courses/77/pages/week-4"})
        if path in ("/api/v1/courses/77/files/900", "/api/v1/courses/77/files/901", "/api/v1/courses/77/files/902"):
            fid = path.rsplit("/", 1)[1]
            name = {"900": "hw4.pdf", "901": "slides-week4.pdf", "902": "answers.pdf"}[fid]
            # 900 is link-only (hidden from the Files list but downloadable); 902 is locked until later.
            return page({"id": int(fid), "display_name": name, "size": 2000, "content-type": "application/pdf",
                         "updated_at": "2026-10-20T00:00:00Z", "url": f"{CANVAS}/files/{fid}/download?verifier=v",
                         "hidden_for_user": fid == "900", "locked_for_user": fid == "902"})  # fmt: skip
        if path.startswith("/files/"):
            return httpx.Response(302, headers={"Location": "https://files.example.net/blob?sig=abc"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_canvas_documents():
    seen: list[httpx.Request] = []
    problems: list[str] = []
    config = CanvasConfig(base_url=CANVAS, cookie="canvas_session=test")
    async with httpx.AsyncClient(transport=canvas_transport(seen)) as http:
        got = [f async for f in fetch_canvas(CanvasApi(config, http), [("math300", 77)], {}, 2**20, problems)]
    by_key = {f.key: f for f in got}
    assert set(by_key) == {"77:syllabus", "77:assignment:5", "77:page:week-4", "77:file:900", "77:file:901"}
    hw = by_key["77:assignment:5"]
    assert (hw.item_source_id, hw.kind) == ("assignment:5", "assignment")
    assert hw.text.startswith("Due: 2026-10-30T06:59:00Z\nPoints: 10\nSubmit: online_upload")
    assert by_key["77:file:900"].content.startswith(b"%PDF-")
    assert by_key["77:file:900"].kind == "assignment" and by_key["77:file:901"].kind == "slides"
    # The session cookie goes to Canvas only, never to the file store it redirects to.
    for r in seen:
        assert ("cookie" in r.headers) == (r.url.host == "canvas.example.edu")
    assert problems == ["answers.pdf: locked on Canvas"]

    # Unchanged files aren't downloaded again.
    seen.clear()
    known = {"77:file:900": "2026-10-20T00:00:00Z", "77:file:901": "2026-10-20T00:00:00Z"}
    async with httpx.AsyncClient(transport=canvas_transport(seen)) as http:
        got = [f async for f in fetch_canvas(CanvasApi(config, http), [("math300", 77)], known, 2**20, problems)]
    assert all(f.unchanged for f in got if ":file:" in f.key)
    assert not any(r.url.host == "files.example.net" for r in seen)


@pytest.mark.anyio
async def test_canvas_documents_need_a_live_session():
    config = CanvasConfig(base_url=CANVAS, cookie="canvas_session=old")
    async with httpx.AsyncClient(transport=canvas_transport([], expired=True)) as http:
        with pytest.raises(SourceError, match="fresh session cookie"):
            [f async for f in fetch_canvas(CanvasApi(config, http), [("math300", 77)], {}, 2**20, [])]


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text("courses:\n  - code: CSE 410\nsources: []\n")
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        yield c


def test_adding_a_textbook_and_notes(client):
    book = pdf("Chapter 8: Address translation", "8.2 Page tables")
    r = client.post("/api/library/upload", files={"file": ("ospp.pdf", book, "application/pdf")},
                    data={"course_id": "cse410", "kind": "textbook", "title": "OSPP"})  # fmt: skip
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (doc["title"], doc["kind"], doc["pages"], doc["has_file"], doc["has_text"]) == ("OSPP", "textbook", 2, True, True)
    assert "8.2 Page tables" in client.get(f"/api/library/{doc['id']}/text").text
    opened = client.get(f"/api/library/{doc['id']}/open", params={"page": 2}, follow_redirects=False)
    assert opened.headers["location"] == f"/api/library/{doc['id']}/file#page=2"
    assert client.get(f"/api/library/{doc['id']}/file").content == book

    note = client.post("/api/library/text", json={"title": "Midterm scope", "text": "Chapters 1-9, no locks", "course_id": "cse410"}).json()
    assert note["kind"] == "notes" and note["has_text"] and not note["has_file"]

    listed = client.get("/api/library").json()
    assert {d["title"] for d in listed["documents"]} == {"OSPP", "Midterm scope"}
    index = (client.app.state.library.root / "INDEX.md").read_text(encoding="utf-8")
    assert "OSPP" in index and "Midterm scope" in index

    assert client.delete(f"/api/library/{doc['id']}").status_code == 204
    assert [d["title"] for d in client.get("/api/library").json()["documents"]] == ["Midterm scope"]
    assert client.post("/api/library/text", json={"title": "x", "text": "y", "course_id": "nope"}).status_code == 422


def test_course_sites_are_read_unless_listed_otherwise(tmp_path):
    from app.config import AppConfig
    from app.library import LibrarySync

    config = AppConfig.model_validate({
        "courses": [{"code": "CSE 410", "links": {"site": ROOT}}, {"code": "MATH 300"}, {"code": "CSE 333", "links": {"site": "https://x/333/"}}],
        "library": {"sites": [{"course": "CSE 333", "url": "https://x/333/", "max_pages": 5}]},
    })  # fmt: skip
    sync = LibrarySync(None, config, None, None, None, list)
    assert [(s.course, s.url, s.max_pages) for s in sync.sites] == [("CSE 333", "https://x/333/", 5), ("CSE 410", ROOT, 200)]
    assert sync.fetchers == ["site:cse333", "site:cse410"]
