"""A look around without any accounts: made-up courses, deadlines and grades, a day's plan, a tutoring
session and a whiteboard, in a throwaway folder.

    cd backend && python -m app.demo     # then open http://localhost:8765 (or run the frontend's npm run dev)

Every course, deadline, grade and message is invented. It reads no .env and no config.yaml, makes no requests
(the only source is the demo one), and runs without the agent, so the planner and tutor say they aren't set up.
Its data goes to a temp folder that's deleted when you stop it with Ctrl+C; set SCHOOL_DEMO_DATA to keep it
somewhere else (the demo refuses a folder that already holds other data). It serves frontend/dist if you've
built it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from .config import Settings, load_config
from .courses import sync_config_courses
from .db import make_engine, make_sessionmaker
from .migrate import upgrade
from .models import Canvas, Conversation, Message, Plan

TZ = "America/Los_Angeles"
CONFIG = f"""# The demo: made-up courses only (see app/sources/demo.py).
timezone: {TZ}
courses:
  - code: DEMO 101
    grading:
      categories:
        - name: Problem sets
          weight: 60
          match: problem set
        - name: Reading
          weight: 25
        - name: Final
          weight: 15
          match: final exam
  - code: DEMO 202
    grading:
      categories:
        - name: Labs
          weight: 70
        - name: Quizzes
          weight: 30
sources:
  - type: demo
"""
STATIC = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def seed(config_path: Path, data_dir: Path) -> None:
    """Today's plan and a tutoring session: what the agent would have made, written by hand."""
    engine = make_engine(data_dir / "dashboard.db")
    upgrade(engine)
    Session = make_sessionmaker(engine)
    sync_config_courses(Session, load_config(config_path).courses)
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(TZ)).date()
    with Session.begin() as s:
        s.add(Plan(
            date=today.isoformat(), status="ready", created_at=now, finished_at=now, model="demo",
            summary="Problem set 3 is due tonight, so it comes first. Then a start on the shell lab while "
                    "Thursday's lecture is fresh.",
            tasks=[
                {"title": "Problem set 3: finish problems 4-6, then check each base case by hand", "minutes": 75,
                 "course": "DEMO 101", "when": "morning", "why": "Due tonight at 11:59pm.", "resources": []},
                {"title": "Lab 2: read the spec and sketch how the shell parses a pipeline", "minutes": 60,
                 "course": "DEMO 202", "when": "afternoon", "resources": [],
                 "why": "Due tomorrow at 6pm, and the parser is the long part."},
                {"title": "Midterm: redo two problems from last year's exam without notes", "minutes": 40,
                 "course": "DEMO 303", "when": "evening", "resources": [],
                 "why": "The midterm is in four days; this shows what to review first."},
            ],
            heads_up=["DEMO 303's midterm is on " + (today + timedelta(days=4)).strftime("%A") + ": start "
                      "reviewing this weekend.", "Quiz 2 for DEMO 202 is marked missing."],
        ))  # fmt: skip
        conv = Conversation(title="Why does my recursive sum never stop?", mode="explain", course_id="demo101",
                            created_at=now - timedelta(minutes=20), updated_at=now - timedelta(minutes=5))  # fmt: skip
        s.add(conv)
        s.flush()
        turns = [
            ("user", "Why does my recursive sum never stop? It works for small inputs and then crashes."),
            ("assistant", "Before we look at the code: what should `sum(n)` return when $n = 0$?\n\n"
                          "Then write the calls for $n = 3$ by hand, one line each:\n\n"
                          "$$\n\\operatorname{sum}(3) = 3 + \\operatorname{sum}(2)\n$$\n\n"
                          "Where does your version stop?"),
            ("user", "It should be 0. But mine never checks for 0, so it keeps going to -1, -2, ..."),
            ("assistant", "That's it: the check you're missing is the **base case**. Add it and try $n = 0$ "
                          "and $n = 3$ yourself.\n\nThen a harder one: what should happen for a negative $n$, "
                          "and does your fix handle it?"),
        ]  # fmt: skip
        for i, (role, text) in enumerate(turns):
            s.add(Message(conversation_id=conv.id, role=role, status="ready", text=text,
                          created_at=now - timedelta(minutes=20 - 5 * i)))  # fmt: skip
        seed_canvas(s, now)
    engine.dispose()


# Colors as the canvas page stores them: Excalidraw's dark mode inverts the drawing, so the student's ink shows
# as the page's text color and the tutor's as foam on a dark note (frontend/src/canvas/).
PEN, INK, NOTE_FILL = "#0f0d26", "#003b46", "#e8e5ff"
HAND, NOTE_FONT = 5, 6  # Excalidraw's Excalifont and Nunito
VIEW = {"scrollX": 290, "scrollY": 290, "zoom": {"value": 1}}  # clear of the style panel and the pinned problem


def _text(id: str, x: float, y: float, text: str, size: int = 28, **extra) -> dict:
    lines = text.split("\n")
    return {"type": "text", "id": id, "x": x, "y": y, "text": text, "originalText": text, "fontSize": size,
            "fontFamily": HAND, "textAlign": "left", "verticalAlign": "top", "strokeColor": PEN, "lineHeight": 1.25,
            "width": max(map(len, lines)) * size * 0.52, "height": len(lines) * size * 1.25, **extra}  # fmt: skip


def seed_canvas(s, now: datetime) -> None:
    """A Tutor session's whiteboard partway through a proof: the problem pinned, the tutor's note by the stuck line."""
    conv = Conversation(title="Induction proofs", mode="explain", course_id="demo101",
                        created_at=now - timedelta(minutes=12), updated_at=now - timedelta(minutes=2))  # fmt: skip
    s.add(conv)
    s.flush()
    turns = [
        ("user", "Let's do PS3 problem 5 on the canvas", {"kind": "setup"}),
        ("assistant", "It's pinned at the top: an induction proof. Start with the base case, and press Help "
                      "when you want a nudge.", None),
        ("user", "Help", {"kind": "help"}),
        ("assistant", "Your base case and the setup of the step are right. The last line is where it stalls: "
                      "look at the two terms you're adding. I left a note there.", None),
    ]  # fmt: skip
    replies = []
    for i, (role, text, sent) in enumerate(turns):
        m = Message(conversation_id=conv.id, role=role, status="ready", text=text, canvas=sent,
                    created_at=now - timedelta(minutes=12 - 3 * i))  # fmt: skip
        s.add(m)
        s.flush()
        if role == "assistant":
            replies.append(m.id)
    mark = {"groupIds": [f"tutor-{replies[-1]}-0"], "customData": {"tutor": replies[-1]}}
    note = "Both terms share a factor.\nWhich one? Pull it out\nand see what's left."
    elements = [
        _text("work-1", 0, 0, "Base case: n = 1\n    1 = 1(1+1)/2 = 1   ✓"),
        _text("work-2", 0, 120, "Step: assume 1 + 2 + ... + k = k(k+1)/2"),
        _text("work-3", 0, 190, "1 + 2 + ... + k + (k+1) = k(k+1)/2 + (k+1)\n                        = ?"),
        {"type": "rectangle", "id": "note", "x": 600, "y": 232, "width": 270, "height": 92, "strokeColor": INK,
         "backgroundColor": NOTE_FILL, "fillStyle": "solid", "strokeWidth": 1, "roughness": 0,
         "roundness": {"type": 3}, "boundElements": [{"type": "text", "id": "note-text"}], **mark},
        _text("note-text", 605, 237, note, 18, fontFamily=NOTE_FONT, strokeColor=INK, containerId="note", **mark),
        {"type": "arrow", "id": "note-arrow", "x": 600, "y": 262, "width": 240, "height": 12,
         "points": [[0, 0], [-240, -12]], "strokeColor": INK, "strokeWidth": 2, "roughness": 0,
         "startArrowhead": None, "endArrowhead": "arrow", **mark},
    ]  # fmt: skip
    problem = ("**Problem set 3, problem 5.** Prove by induction that for every $n \\ge 1$,\n\n"
               "$$\n1 + 2 + \\cdots + n = \\frac{n(n+1)}{2}.\n$$")  # fmt: skip
    s.add(Canvas(title=conv.title, course_id="demo101", conversation_id=conv.id, scene={"elements": elements, "appState": VIEW},
                 state={"applied": replies, "frames": {}, "problem": problem}, created_at=conv.created_at,
                 updated_at=conv.updated_at))  # fmt: skip


def main() -> None:
    chosen = os.environ.get("SCHOOL_DEMO_DATA")
    root = Path(chosen) if chosen else Path(tempfile.mkdtemp(prefix="school-demo-"))
    if chosen and root.exists() and any(p.name != "demo" for p in root.iterdir()):
        raise SystemExit(f"{root} already holds other data; point SCHOOL_DEMO_DATA at an empty folder.")
    folder = root / "demo"
    fresh = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    config_path = folder / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    if fresh:
        seed(config_path, folder / "data")
    settings = Settings(config_path=config_path, data_dir=folder / "data", sync_on_start=True,
                        static_dir=STATIC if STATIC.is_dir() else None, agent_url=None)  # fmt: skip
    from .main import create_app

    print("The demo, on made-up data: http://localhost:8765" + ("" if STATIC.is_dir() else
          "  (build the frontend first, or run npm run dev in frontend/ and open http://localhost:5173)"))  # fmt: skip
    try:
        uvicorn.run(create_app(settings), host="127.0.0.1", port=8765)
    finally:
        if not chosen:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
