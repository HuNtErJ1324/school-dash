from __future__ import annotations

import importlib.util
import json
import subprocess
import time as clock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent import runner
from app.agent.client import AgentReply
from app.agent.context import snapshot_db
from app.config import Settings
from app.main import create_app
from app.models import Brief, Item
from app.sources.base import CourseRef, FetchResult, ItemIn
from app.sync import store_result

LA = ZoneInfo("America/Los_Angeles")


# -- the runner, inside the agent container ----------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    home, ws = tmp_path / "home", tmp_path / "workspace"
    monkeypatch.setattr(runner, "HOME", str(home))
    monkeypatch.setattr(runner, "WORKSPACE", str(ws))
    monkeypatch.setattr(runner, "THREADS_PATH", str(home / "school-threads.json"))
    monkeypatch.setattr(runner, "OUTBOX", str(ws / "outbox"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("PRIME_AGENT_BIN", str(tmp_path / "prime-agent"))
    (tmp_path / "prime-agent").write_text("")
    runner.prepare_workspace()
    return ws


def load_tools(ws: Path):
    spec = importlib.util.spec_from_file_location("school", ws / "school.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workspace_has_instructions_tools_and_models(workspace):
    agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Academic integrity" in agents
    # Tutoring turns on the scaffold/rigor dial, with both of its failure modes spelled out.
    assert "### The dial: scaffold or push for rigor" in agents
    assert "- Over-scaffolding: you do the thinking." in agents and "- Missed rigor:" in agents
    # A gap a rigor push turns up starts from what the student knows, not a re-explanation.
    assert "- When a push for rigor turns up a gap" in agents and "don't re-teach yet" in agents
    assert "not at what they missed" in agents
    # The canvas protocol the Canvas tab draws from (frontend/src/canvas/annotate.ts).
    assert "### The shared canvas" in agents and '"point_to": [x, y]' in agents and '{"circle": [x, y' in agents
    assert (workspace / "school.py").exists() and (workspace / "notes").is_dir()
    models = json.loads(Path(runner.HOME, ".prime", "agent", "models.json").read_text())
    opus = next(m for m in models["providers"]["anthropic"]["models"] if m["id"] == "claude-opus-5-5")
    assert opus["cost"] == {"input": 4, "output": 20, "cacheRead": 0.2, "cacheWrite": 5}  # or every run looks free


def events(*lines: dict[str, Any]) -> str:
    return "\n".join(json.dumps(e) for e in lines) + "\n"


def test_ask_parses_prime_agent_output_and_returns_saved_results(workspace, monkeypatch):
    calls = []
    tools = load_tools(workspace)

    def fake_run(cmd, **kw):
        calls.append(cmd)
        tools.save_plan("2026-10-24", "A lab day.", [{"title": "Lab 3: read the spec", "minutes": 50, "item_id": 7}])
        out = events(
            {"type": "session", "id": "sess-1"},
            {"type": "tool_execution_start"},
            {"type": "message_end", "message": {"role": "assistant", "model": "claude-opus-5-5",
                                                "content": [{"type": "text", "text": "Planned."}],
                                                "usage": {"cost": {"total": 0.42}}}},
        )  # fmt: skip
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    res = runner.ask("plan:2026-10-24", "Make my plan", "system", "claude-opus-5-5")
    assert (res["text"], res["session"], res["cost"], res["tool_calls"]) == ("Planned.", "sess-1", 0.42, 1)
    assert res["ops"][0]["op"] == "plan" and res["ops"][0]["tasks"][0]["item_id"] == 7
    assert calls[0][:2] == [str(Path(runner.WORKSPACE).parent / "prime-agent"), "-p"]
    assert "--resume" not in calls[0]
    assert not list((workspace / "outbox").iterdir())  # handed back once, not again

    runner.ask("plan:2026-10-24", "Make it lighter", "system", "claude-opus-5-5")
    assert calls[1][calls[1].index("--resume") + 1] == "sess-1"


def test_ask_needs_an_api_key(workspace, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        runner.ask("t", "p", "s", "claude-opus-5-5")


def test_tools_validate_what_the_agent_saves(workspace):
    tools = load_tools(workspace)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        tools.save_plan("today", "x", [{"title": "a"}])
    with pytest.raises(ValueError, match="title"):
        tools.save_plan("2026-10-24", "x", [{"minutes": 30}])
    with pytest.raises(ValueError, match="doc_id or an http"):
        tools.save_brief(3, "# Lab 3", resources=[{"title": "Spec"}])
    assert "Saved" in tools.save_brief(3, "# Lab 3", 600, [{"title": "Spec", "doc_id": 4, "pages": "2-3"}])


# -- the dashboard side, with a stand-in for the agent container --------------------------------


class FakeAgent:
    """Answers like the runner does. `script` decides what each request saves."""

    enabled = True

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, str]] = []
        self.script: Any = lambda thread, prompt: AgentReply(text="ok")
        self.healthy = {"ok": True, "prime_agent": True, "api_key": True, "busy": False}

    async def health(self) -> dict[str, Any]:
        return self.healthy

    async def ask(self, thread: str, prompt: str, system: str) -> AgentReply:
        self.asked.append((thread, prompt, system))
        return self.script(thread, prompt)

    async def reset(self, thread: str) -> None:
        pass


CONFIG = """
timezone: America/Los_Angeles
courses:
  - code: CSE 410
planner:
  hours: {default: 3}
  notes: I work Tuesday evenings.
sources:
  - type: demo
"""


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False,
                        agent_url="http://agent:8790")  # fmt: skip
    with TestClient(create_app(settings)) as c:
        fake = FakeAgent()
        c.app.state.assistant.client = fake
        now = datetime.now(timezone.utc)
        lab = ItemIn(source_id="lab3", kind="assignment", title="Lab 3", course=CourseRef(code="CSE 410"),
                     due_at=now + timedelta(days=4), status="pending", points=25)  # fmt: skip
        lecture = ItemIn(source_id="lec", kind="lecture", title="Page tables", course=CourseRef(code="CSE 410"),
                         starts_at=now)  # fmt: skip
        store_result(c.app.state.Session, "demo", FetchResult(items=[lab, lecture]), now)
        c.fake = fake
        yield c


def wait(client: TestClient, url: str, done: Any, timeout: float = 5) -> Any:
    end = clock.monotonic() + timeout
    while clock.monotonic() < end:
        body = client.get(url).json()
        if done(body):
            return body
        clock.sleep(0.02)
    raise AssertionError(f"{url} never finished: {body}")


def lab_id(client: TestClient) -> int:
    with client.app.state.Session() as s:
        return s.scalars(select(Item.id).where(Item.source_id == "lab3")).one()


def test_making_a_plan(client):
    lab = lab_id(client)

    def script(thread, prompt):
        return AgentReply(text="A lab day.", cost=0.31, model="claude-opus-5-5", ops=[
            {"op": "brief", "item_id": lab, "markdown": "## What it asks\nPage tables.", "minutes": 600, "resources": []},
            {"op": "plan", "date": "x", "summary": "Start Lab 3.", "heads_up": ["Midterm in 9 days"], "tasks": [
                {"title": "Lab 3: read the spec", "minutes": 50, "course": "CSE 410", "item_id": lab,
                 "resources": [{"title": "Lab 3 spec", "doc_id": 4, "pages": "1-3"}]},
                {"title": "Review lecture 9", "minutes": 30},
            ]},
        ])  # fmt: skip

    client.fake.script = script
    started = client.post("/api/plans", json={"note": "I only have two hours"})
    assert started.status_code == 202 and started.json()["status"] == "running"
    today = wait(client, "/api/plans/today", lambda b: b["plan"] is not None)
    plan = today["plan"]
    assert (plan["summary"], plan["note"], plan["cost"]) == ("Start Lab 3.", "I only have two hours", 0.31)
    assert [t["minutes"] for t in plan["tasks"]] == [50, 30] and plan["heads_up"] == ["Midterm in 9 days"]
    assert plan["tasks"][0]["resources"][0] == {"title": "Lab 3 spec", "doc_id": 4, "pages": "1-3", "url": None}
    assert today["pending"] is None

    thread, prompt, system = client.fake.asked[0]
    assert thread == f"plan:{plan['date']}"
    assert f"[item {lab}] CSE 410 · Lab 3 (assignment)" in prompt and "25 pts · not submitted" in prompt
    assert "For this plan specifically: I only have two hours" in prompt
    assert "about 3 hours" in prompt and "I work Tuesday evenings." in prompt
    assert "CSE 410 lecture: Page tables" in prompt
    assert "AGENTS.md" in system

    # The agent saw a fresh snapshot, and the brief it saved along the way is kept.
    data = client.app.state.assistant.snapshot_dir
    ctx = json.loads((data / "context.json").read_text(encoding="utf-8"))
    assert any(i["title"] == "Lab 3" and i["kind"] == "assignment" for i in ctx["items"])
    assert (data / "dashboard.db").exists()
    assert client.get(f"/api/items/{lab}/brief").json()["brief"]["minutes"] == 600

    ticked = client.patch(f"/api/plans/{plan['id']}/tasks/0", json={"done": True}).json()
    assert [t["done"] for t in ticked["tasks"]] == [True, False]

    # A re-plan that fails leaves the good plan in place and says why.
    client.fake.script = lambda thread, prompt: AgentReply(text="Sorry.")
    client.post("/api/plans", json={})
    today = wait(client, "/api/plans/today", lambda b: b["pending"] and b["pending"]["status"] == "error")
    assert today["plan"]["id"] == plan["id"]
    assert today["pending"]["error"] == "The agent finished without saving a plan."
    assert "Already done today (from earlier plans):\n- Lab 3: read the spec" in client.fake.asked[1][1]


def test_writing_a_brief(client):
    lab = lab_id(client)
    client.fake.script = lambda thread, prompt: AgentReply(text="Done.", ops=[
        {"op": "brief", "item_id": lab, "markdown": "## What it asks\n...", "minutes": 480,
         "resources": [{"title": "OSPP 8.2", "doc_id": 17, "pages": "412"}]},
    ])  # fmt: skip
    assert client.get(f"/api/items/{lab}/brief").json() == {"brief": None, "pending": None}
    assert client.post(f"/api/items/{lab}/brief").json()["status"] == "running"
    brief = wait(client, f"/api/items/{lab}/brief", lambda b: b["brief"] is not None)["brief"]
    assert (brief["title"], brief["minutes"], brief["resources"][0]["doc_id"]) == ("CSE 410 · Lab 3", 480, 17)
    assert client.fake.asked[0][0] == f"brief:{lab}"
    assert "No library document is tied to it yet" in client.fake.asked[0][1]
    assert [b["item_id"] for b in client.get("/api/briefs").json()] == [lab]
    assert client.post("/api/items/99999/brief").status_code == 404


def test_conversations(client):
    lab = lab_id(client)
    client.fake.script = lambda thread, prompt: AgentReply(text=f"Answer to: {prompt[:20]}", cost=0.05)
    conv = client.post("/api/conversations", json={"text": "What does Lab 3 want?", "item_id": lab}).json()
    done = wait(client, f"/api/conversations/{conv['id']}", lambda c: c["messages"][-1]["status"] != "running")
    assert [(m["role"], m["status"]) for m in done["messages"]] == [("user", "ready"), ("assistant", "ready")]
    assert done["messages"][1]["text"] == "Answer to: What does Lab 3 want"
    assert "(Asked from the dashboard; course CSE 410; about [item" in client.fake.asked[0][1]
    assert (done["course_id"], done["mode"]) == ("cse410", "ask")  # the item's course

    client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "And part B?"})
    done = wait(client, f"/api/conversations/{conv['id']}", lambda c: len(c["messages"]) == 4 and c["messages"][-1]["status"] != "running")
    assert client.fake.asked[1][:2] == (f"chat:{conv['id']}", "And part B?")
    assert [c["title"] for c in client.get("/api/conversations").json()] == ["What does Lab 3 want?"]
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert client.get("/api/conversations").json() == []


def test_tutoring_sessions(client):
    client.fake.script = lambda thread, prompt: AgentReply(text="Question 1: what does the TLB cache?")
    conv = client.post("/api/conversations", json={"text": "Virtual memory", "course_id": "cse410", "mode": "quiz"}).json()
    assert (conv["course_id"], conv["mode"]) == ("cse410", "quiz")
    wait(client, f"/api/conversations/{conv['id']}", lambda c: c["messages"][-1]["status"] != "running")
    first = client.fake.asked[0][1]
    assert first.startswith("[Tutoring session: Quiz me (course CSE 410)]\nQuiz me on this. Ask one question at a time")
    assert first.endswith("\n\nVirtual memory") and "never open graded homework" in first
    # After a right answer it raises the bar; after a wrong one the student looks for the error first.
    assert "If I'm right, raise the bar" in first and "have me find my error before you explain it" in first

    # Later turns are just the student's words: the session already knows its mode.
    client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "Page table entries"})
    wait(client, f"/api/conversations/{conv['id']}", lambda c: len(c["messages"]) == 4 and c["messages"][-1]["status"] != "running")
    assert client.fake.asked[1][1] == "Page table entries"
    assert [(c["mode"], c["course_id"]) for c in client.get("/api/conversations").json()] == [("quiz", "cse410")]

    assert client.post("/api/conversations", json={"text": "x", "mode": "cram"}).status_code == 422
    assert client.post("/api/conversations", json={"text": "x", "course_id": "cse999"}).status_code == 422


def test_agent_problems_show_up_on_the_job(client):
    from app.agent.client import AgentError

    def broken(thread, prompt):
        raise AgentError("Claude rejected the API key. Check ANTHROPIC_API_KEY in .env.")

    client.fake.script = broken
    client.post("/api/plans", json={})
    pending = wait(client, "/api/plans/today", lambda b: b["pending"] and b["pending"]["status"] == "error")["pending"]
    assert pending["error"].startswith("Claude rejected the API key")

    client.fake.healthy = {"ok": True, "prime_agent": True, "api_key": False}
    status = client.get("/api/agent").json()
    assert (status["ready"], status["model"]) == (False, "claude-opus-5-5")
    assert "ANTHROPIC_API_KEY" in status["problem"]


def test_morning_writes_briefs_then_the_plan(client):
    assistant = client.app.state.assistant
    lab = lab_id(client)
    assert assistant.briefs_wanted() == [lab]

    def script(thread, prompt):
        if thread.startswith("brief:"):
            return AgentReply(text="ok", ops=[{"op": "brief", "item_id": lab, "markdown": "brief", "resources": []}])
        return AgentReply(text="ok", ops=[{"op": "plan", "summary": "s", "tasks": [{"title": "t"}]}])

    client.fake.script = script
    client.portal.call(assistant.morning)
    assert [t for t, _, _ in client.fake.asked] == [f"brief:{lab}", f"plan:{assistant.today()}"]
    assert assistant.briefs_wanted() == []
    assert not assistant.missed_today()

    # No API key: no morning run, and nothing spent.
    client.fake.asked.clear()
    client.fake.healthy = {"ok": True, "prime_agent": True, "api_key": False}
    client.portal.call(assistant.morning)
    assert client.fake.asked == []


def test_planning_time(client):
    assistant = client.app.state.assistant
    assistant.now = lambda: datetime(2026, 10, 24, 5, 0, tzinfo=LA)
    assert assistant.next_morning() == datetime(2026, 10, 24, 6, 30, tzinfo=LA)
    assert not assistant.missed_today()
    assistant.now = lambda: datetime(2026, 10, 24, 9, 0, tzinfo=LA)
    assert assistant.next_morning() == datetime(2026, 10, 25, 6, 30, tzinfo=LA)
    assert assistant.missed_today()


def test_interrupted_jobs_are_marked_failed(client):
    assistant = client.app.state.assistant
    with client.app.state.Session.begin() as s:
        s.add(Brief(item_id=1, title="x", status="running", resources=[], created_at=datetime.now(timezone.utc)))
    assistant.recover()
    with client.app.state.Session() as s:
        assert s.scalars(select(Brief.status)).one() == "error"


def test_snapshot_is_a_consistent_copy(tmp_path):
    import sqlite3

    src = tmp_path / "live.db"
    with sqlite3.connect(src) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (1)")
    snapshot_db(src, tmp_path / "snap" / "dashboard.db")
    # Not WAL: the agent opens it from a read-only mount, where a WAL database can't be read.
    c = sqlite3.connect(f"file:{tmp_path / 'snap' / 'dashboard.db'}?mode=ro", uri=True)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert c.execute("SELECT x FROM t").fetchall() == [(1,)]
    finally:
        c.close()


def test_the_app_starts_with_its_background_loops(tmp_path):
    (tmp_path / "config.yaml").write_text("sources: []\n")
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=True,
                        agent_url="http://agent:8790")  # fmt: skip
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/agent").json()["enabled"] is True
        assert c.get("/api/library").json()["running"] is False


def test_page_images_for_scanned_documents(workspace):
    import pymupdf

    from app.db import make_engine, make_sessionmaker
    from app.migrate import upgrade
    from app.models import Course, Document

    files = workspace / "library" / "math208" / "canvas" / "files"
    files.mkdir(parents=True)
    scan = pymupdf.open()
    for _ in range(2):  # pages with drawing but no text, like handwritten notes
        scan.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 200), color=(0, 0, 0))
    (files / "s7.5a.pdf").write_bytes(scan.tobytes())
    (files / "s7.5a.pdf.txt").write_text("# s7.5a\n\n--- page 1 ---\n\n--- page 2 ---\n", encoding="utf-8")
    (workspace / "library" / "math208" / "canvas" / "syllabus.md").write_text("# Syllabus\n", encoding="utf-8")
    engine = make_engine(workspace / "data" / "dashboard.db")
    upgrade(engine)
    now = datetime.now(timezone.utc)
    with make_sessionmaker(engine).begin() as s:
        s.add(Course(id="math208", code="MATH 208", links={}))
        s.add(Document(id=57, source="canvas", key="a", course_id="math208", title="Class Notes (s7.5a.pdf)", kind="slides",
                       path="math208/canvas/files/s7.5a.pdf", text_path="math208/canvas/files/s7.5a.pdf.txt", pages=2,
                       note="little or no text layer (scanned or image slides): open the PDF to read it",
                       fetched_at=now, updated_at=now))  # fmt: skip
        s.add(Document(id=58, source="canvas", key="b", course_id="math208", title="Syllabus", kind="syllabus",
                       text_path="math208/canvas/syllabus.md", fetched_at=now, updated_at=now))  # fmt: skip
    engine.dispose()  # checkpoints the WAL, as the dashboard's snapshot does
    tools = load_tools(workspace)

    path = tools.page_image(57, 2)
    assert path == "pages/57-p2-120.png"
    first = (workspace / path).stat().st_mtime_ns
    assert (workspace / path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert tools.page_image(57, 2) == path and (workspace / path).stat().st_mtime_ns == first  # reused
    with pytest.raises(ValueError, match="outside this document's pages 1-2"):
        tools.page_image(57, 9)
    with pytest.raises(ValueError, match="isn't a PDF"):
        tools.page_image(58, 1)
    assert "is a scan" in tools.read(57) and "is a scan" not in tools.read(58)


def test_photos_in_tutoring_sessions(client):
    import io

    from PIL import Image

    # A phone photo taken sideways: stored 3000x2000 with "rotate 90°" in its EXIF, plus camera details.
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation
    exif[0x010F] = "TestPhone"  # make
    raw = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(raw, "JPEG", exif=exif)
    r = client.post("/api/uploads", files={"file": ("IMG_1234.jpg", raw.getvalue(), "image/jpeg")})
    assert r.status_code == 201, r.text
    photo = r.json()
    assert photo["height"] == 1600 and photo["width"] in (1066, 1067)  # upright, long side scaled to 1600
    stored = client.get(f"/api/uploads/{photo['id']}")
    with Image.open(io.BytesIO(stored.content)) as back:
        assert back.format == "JPEG" and back.size == (photo["width"], photo["height"])
        assert not dict(back.getexif())  # no orientation, camera or location metadata kept
    assert client.post("/api/uploads", files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 422
    assert client.get("/api/uploads/not-an-id").status_code == 404

    client.fake.script = lambda thread, prompt: AgentReply(text="Your N works, but check the last inequality.")
    conv = client.post("/api/conversations", json={"text": "Is my proof right?", "course_id": "cse410", "mode": "quiz",
                                                   "photos": [photo["id"]]}).json()  # fmt: skip
    done = wait(client, f"/api/conversations/{conv['id']}", lambda c: c["messages"][-1]["status"] != "running")
    assert done["messages"][0]["photos"] == [photo["id"]] and done["messages"][1]["photos"] == []
    assert client.fake.asked[0][1].endswith(
        f'\n\n[The student attached 1 photo. Look before replying: await attach_image("uploads/{photo["id"]}.jpg")]'
    )

    client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "Thanks"})
    wait(client, f"/api/conversations/{conv['id']}", lambda c: len(c["messages"]) == 4 and c["messages"][-1]["status"] != "running")
    assert client.fake.asked[1][1] == "Thanks"  # no photos, no pointer
    unknown = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "and this", "photos": ["0" * 32]})
    assert unknown.status_code == 422
    too_many = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "x", "photos": [photo["id"]] * 5})
    assert too_many.status_code == 422


def test_explain_sessions_start_from_what_the_student_knows():
    from app.agent.prompts import chat_prompt

    first = chat_prompt("Page tables", None, LA, first=True, mode="explain", course="CSE 410")
    assert first.startswith("[Tutoring session: Explain (course CSE 410)]\nHelp me understand this.")
    assert "finding out what I already know" in first and "step by step" not in first


def png(width: int = 40, height: int = 30, color: str = "white") -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def test_canvases_keep_their_drawing_and_images(client):
    c = client.post("/api/canvases", json={"title": "HW 3 #2", "course_id": "cse410"}).json()
    assert c["title"] == "HW 3 #2" and c["scene"] == {} and c["files"] == [] and c["conversation_id"] is None
    assert client.post("/api/canvases", json={"course_id": "nope"}).status_code == 422

    stroke = {"id": "a1", "type": "freedraw", "x": 10, "y": 20, "points": [[0, 0], [5, 5]]}
    scene = {"elements": [stroke], "appState": {"viewBackgroundColor": "#ffffff"}, "state": {"applied": [7]}}
    assert client.put(f"/api/canvases/{c['id']}/scene", json=scene).status_code == 204
    image = png()
    r = client.put(f"/api/canvases/{c['id']}/files/f-1", content=image, headers={"Content-Type": "image/png"})
    assert r.status_code == 201 and r.json() == {"id": "f-1", "mime_type": "image/png"}
    back = client.get(f"/api/canvases/{c['id']}").json()
    assert back["scene"] == {"elements": [stroke], "appState": {"viewBackgroundColor": "#ffffff"}}
    assert back["state"] == {"applied": [7]} and back["files"] == [{"id": "f-1", "mime_type": "image/png"}]
    got = client.get(f"/api/canvases/{c['id']}/files/f-1")
    assert got.content == image and got.headers["content-type"] == "image/png"

    # Only images, under ids that can't climb out of the canvas's folder.
    jpeg_label = client.put(f"/api/canvases/{c['id']}/files/f-2", content=image, headers={"Content-Type": "image/jpeg"})
    assert jpeg_label.status_code == 422
    text = client.put(f"/api/canvases/{c['id']}/files/f-3", content=b"hello", headers={"Content-Type": "image/png"})
    assert text.status_code == 422
    assert client.put(f"/api/canvases/{c['id']}/files/a.b", content=image, headers={"Content-Type": "image/png"}).status_code == 422
    assert client.get(f"/api/canvases/{c['id']}/files/missing").status_code == 404
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert client.put(f"/api/canvases/{c['id']}/files/s", content=svg, headers={"Content-Type": "image/svg+xml"}).status_code == 201
    assert "sandbox" in client.get(f"/api/canvases/{c['id']}/files/s").headers["content-security-policy"]

    assert client.patch(f"/api/canvases/{c['id']}", json={"title": "Tally charts"}).json()["title"] == "Tally charts"
    assert [x["title"] for x in client.get("/api/canvases").json()] == ["Tally charts"]
    assert client.delete(f"/api/canvases/{c['id']}").status_code == 204
    assert client.get(f"/api/canvases/{c['id']}").status_code == 404
    assert not (client.app.state.canvases_dir / str(c["id"])).exists()


def test_tutoring_on_a_canvas(client):
    c = client.post("/api/canvases", json={"course_id": "cse410"}).json()
    url = f"/api/canvases/{c['id']}/ask"
    assert client.post(url, json={"kind": "chat", "text": "  "}).status_code == 422

    client.fake.script = lambda thread, prompt: AgentReply(text='Found it.\n```canvas\n[{"problem": "Count the dogs."}]\n```')
    conv = client.post(url, json={"kind": "setup", "text": "HW 3 problem 2"}).json()
    assert conv["mode"] == "canvas"
    done = wait(client, f"/api/conversations/{conv['id']}", lambda x: x["messages"][-1]["status"] != "running")
    assert done["messages"][-1]["text"].startswith("Found it.")
    setup = client.fake.asked[0][1]
    assert setup.startswith("[Tutoring session: Canvas (course CSE 410)]\nWe're working one problem together")
    assert setup.endswith("\n\n[Canvas: Setup] HW 3 problem 2")
    assert client.get(f"/api/canvases/{c['id']}").json()["conversation_id"] == conv["id"]

    snap = client.post("/api/uploads", files={"file": ("canvas.png", png(1200, 800), "image/png")}).json()
    snapshot = {"id": snap["id"], "width": 1200, "height": 800, "note": [260, 90]}
    again = client.post(url, json={"kind": "help", "snapshot": snapshot}).json()
    assert again["id"] == conv["id"]  # the same conversation, not a new one
    done = wait(client, f"/api/conversations/{conv['id']}", lambda x: len(x["messages"]) == 4 and x["messages"][-1]["status"] != "running")
    help_msg = done["messages"][2]
    assert help_msg["text"] == "Help" and help_msg["photos"] == [snap["id"]]
    assert help_msg["canvas"] == {"kind": "help", "snapshot": snap["id"], "size": [1200, 800], "note": [260, 90]}
    assert client.fake.asked[1][1] == (
        "[Canvas: Help]\n[Snapshot of the canvas, 1200×800 px; a note takes about 260×90 px of it. Look before "
        f'replying: await attach_image("uploads/{snap["id"]}.jpg")]'
    )

    # Canvas sessions live on the Canvas tab; deleting the canvas deletes its conversation too.
    client.delete(f"/api/canvases/{c['id']}")
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404


def test_a_tutor_session_can_open_a_canvas(client):
    client.fake.script = lambda thread, prompt: AgentReply(text="What do you already know about page tables?")
    conv = client.post("/api/conversations", json={"text": "Page tables", "course_id": "cse410", "mode": "explain"}).json()
    done = wait(client, f"/api/conversations/{conv['id']}", lambda c: c["messages"][-1]["status"] != "running")
    assert done["canvas_id"] is None

    # Opening it makes the session's canvas once; replies already in the session count as drawn.
    url = f"/api/conversations/{conv['id']}/canvas"
    canvas = client.post(url).json()
    assert client.post(url).json()["id"] == canvas["id"]
    assert canvas["title"] == "Page tables" and canvas["course_id"] == "cse410" and canvas["conversation_id"] == conv["id"]
    assert canvas["state"] == {"applied": [done["messages"][1]["id"]]}
    assert client.get(f"/api/conversations/{conv['id']}").json()["canvas_id"] == canvas["id"]
    assert [c["canvas_id"] for c in client.get("/api/conversations").json()] == [canvas["id"]]
    assert client.get("/api/canvases").json() == []  # the Canvas tab lists its own canvases only

    # Help from it goes into the session, which keeps its mode; the prompt says the canvas is open.
    snap = client.post("/api/uploads", files={"file": ("canvas.png", png(600, 400), "image/png")}).json()
    snapshot = {"id": snap["id"], "width": 600, "height": 400, "note": [260, 90]}
    again = client.post(f"/api/canvases/{canvas['id']}/ask", json={"kind": "help", "snapshot": snapshot}).json()
    assert again["id"] == conv["id"] and again["mode"] == "explain"
    wait(client, f"/api/conversations/{conv['id']}", lambda c: len(c["messages"]) == 4 and c["messages"][-1]["status"] != "running")
    assert client.fake.asked[-1][1] == (
        "[Canvas: Help]\n\n[Sent with the session's canvas open beside the chat.]\n[Snapshot of the canvas, 600×400 px; "
        f'a note takes about 260×90 px of it. Look before replying: await attach_image("uploads/{snap["id"]}.jpg")]'
    )
    client.post(f"/api/canvases/{canvas['id']}/ask", json={"kind": "chat", "text": "Is this right?"})
    wait(client, f"/api/conversations/{conv['id']}", lambda c: len(c["messages"]) == 6 and c["messages"][-1]["status"] != "running")
    assert client.fake.asked[-1][1] == "Is this right?\n\n[Sent with the session's canvas open beside the chat.]\n[It's empty so far.]"

    # Deleting the canvas keeps the session; deleting the session takes its canvas along.
    assert client.delete(f"/api/canvases/{canvas['id']}").status_code == 204
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 200
    canvas = client.post(url).json()
    client.put(f"/api/canvases/{canvas['id']}/files/f", content=png(), headers={"Content-Type": "image/png"})
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 204
    assert client.get(f"/api/canvases/{canvas['id']}").status_code == 404
    assert not (client.app.state.canvases_dir / str(canvas["id"])).exists()
