from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import quarters
from app.config import Settings
from app.main import create_app
from app.sources.base import CourseRef, FetchResult, ItemIn
from app.sources.canvas import course_ref
from app.sources.gradescope import parse_courses
from app.sync import store_result

LA = ZoneInfo("America/Los_Angeles")


def test_quarter_codes_names_and_dates():
    assert quarters.code_from_term("Autumn 2026") == quarters.code_from_term("Fall 2026") == "26au"
    assert quarters.code_from_term("Winter 2027") == "27wi"
    assert quarters.code_from_term("2025-2026 Campus Safety Training") is None
    assert quarters.code_from_url("https://courses.cs.washington.edu/courses/cse410/26au/calendar/") == "26au"
    assert quarters.name("27wi") == "Winter 2027"
    assert [quarters.quarter_of(date(2026, m, d)) for m, d in [(3, 31), (4, 1), (6, 16), (9, 1), (12, 31)]] == [
        "26wi", "26sp", "26su", "26au", "26au",
    ]  # fmt: skip
    start, end = quarters.bounds("26au", LA)
    assert (start, end) == (datetime(2026, 9, 1, tzinfo=LA), datetime(2027, 1, 1, tzinfo=LA))
    assert sorted(["26au", "27wi", "26sp"], key=quarters.sort_key) == ["26sp", "26au", "27wi"]


def test_the_current_quarter_waits_for_next_quarters_courses():
    autumn = {"cse410": "26au"}
    assert quarters.current(autumn, date(2026, 11, 2)) == "26au"
    assert quarters.current(autumn, date(2027, 1, 3)) == "26au"  # winter break, no winter courses yet
    assert quarters.current({**autumn, "cse452": "27wi"}, date(2027, 1, 3)) == "27wi"
    assert quarters.current({}, date(2027, 1, 3)) == "27wi"


def test_sources_say_which_quarter_a_course_is_in():
    ref = course_ref({"id": 7, "course_code": "CSE 410 A", "name": "OS", "term": {"name": "Autumn 2026"}}, "https://c")
    assert ref.term == "26au"
    assert course_ref({"id": 8, "course_code": "UW 101", "term": {"name": "2025-2026"}}, "https://c").term is None
    html = """<div id="account-show"><h2 class="pageHeading">Student Courses</h2>
      <div class="courseList--term">Fall 2026</div><a class="courseBox" href="/courses/1"><h3 class="courseBox--shortname">CSE 410</h3></a>
      <div class="courseList--term">Spring 2026</div><a class="courseBox" href="/courses/2"><h3 class="courseBox--shortname">CSE 333</h3></a>
    </div>"""
    assert [(c.short_name, quarters.code_from_term(c.term)) for c in parse_courses(html, all_terms=True)] == [
        ("CSE 410", "26au"), ("CSE 333", "26sp"),
    ]  # fmt: skip


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "courses:\n  - code: MATH 300\n    quarter: 26sp\nsources:\n  - type: demo\n", encoding="utf-8"
    )
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        now = datetime.now(timezone.utc)
        this_quarter = quarters.quarter_of(now.astimezone(LA).date())
        spring = datetime(2026, 5, 4, 17, tzinfo=timezone.utc)
        items = [
            # A source knows CSE 410's quarter.
            ItemIn(source_id="lab3", kind="assignment", title="Lab 3", course=CourseRef(code="CSE 410", term=this_quarter),
                   due_at=now + timedelta(days=3), status="graded", points=10, score=9),
            # Nothing says which quarter CSE 333 was: its dates do.
            ItemIn(source_id="hw1", kind="assignment", title="HW1", course=CourseRef(code="CSE 333"), due_at=spring,
                   status="graded", points=10, score=7),
            # config.yaml places MATH 300.
            ItemIn(source_id="ps1", kind="assignment", title="PS1", course=CourseRef(code="MATH 300"), due_at=spring),
            ItemIn(source_id="party", kind="event", title="End of spring party", starts_at=spring),
        ]  # fmt: skip
        store_result(c.app.state.Session, "demo", FetchResult(items=items), now)
        c.this_quarter = this_quarter
        yield c


def test_quarter_api(client):
    q = client.get("/api/quarters").json()
    assert q["current"] == client.this_quarter
    by_code = {x["code"]: x for x in q["quarters"]}
    assert by_code[client.this_quarter]["courses"] == ["cse410"]
    assert by_code["26sp"] == {"code": "26sp", "name": "Spring 2026", "courses": ["cse333", "math300"]}
    assert [x["code"] for x in q["quarters"]] == sorted(by_code, key=quarters.sort_key, reverse=True)

    courses = client.get("/api/courses", params={"quarter": "26sp"}).json()
    assert [(c["code"], c["term"]) for c in courses] == [("CSE 333", "26sp"), ("MATH 300", "26sp")]
    titles = {i["title"] for i in client.get("/api/items", params={"quarter": "26sp"}).json()}
    assert titles == {"HW1", "PS1", "End of spring party"}  # course-less items go by their date
    assert {i["title"] for i in client.get("/api/items", params={"quarter": client.this_quarter}).json()} == {"Lab 3"}
    assert [g["course"]["code"] for g in client.get("/api/grades", params={"quarter": "26sp"}).json()] == ["CSE 333"]
    assert client.get("/api/items", params={"quarter": "spring"}).status_code == 422
    assert client.get("/api/meta").json()["quarter"] == client.this_quarter


def test_other_quarters_courses_dont_take_this_quarters_colors(client):
    from app.courses import PENCILS, SAFE_TOGETHER, assign_colors
    from app.models import Course

    with client.app.state.Session.begin() as s:
        assign_colors(s, ["demo"])
        colors = {c.id: c.color for c in s.query(Course)}
    assert colors["cse410"] in PENCILS[:SAFE_TOGETHER]  # this quarter's course gets a safe pencil
    assert colors["cse333"] is None  # last spring's, with nothing current, gets none
