from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.coursecode import parse_course_code
from app.sources.base import SourceError
from app.sources.gradescope import GradescopeConfig, GradescopeSource, parse_assignments, parse_courses
from conftest import FIXTURES, LA, fetch

ACCOUNT = (FIXTURES / "gradescope-account.html").read_text()
COURSE = (FIXTURES / "gradescope-course.html").read_text()


def gradescope_server(seen: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/account":
            return httpx.Response(200, text=ACCOUNT)
        if request.url.path == "/courses/1400003":
            return httpx.Response(200, text=COURSE)
        if request.url.path.startswith("/courses/"):
            return httpx.Response(200, text="<table id='assignments-student-table'></table>")
        return httpx.Response(404)

    return handle


def source(**kw) -> GradescopeSource:
    return GradescopeSource("gradescope", GradescopeConfig(cookie="_gradescope_session=abc", pause_seconds=0, **kw))


def test_parse_courses_keeps_the_newest_term():
    courses = parse_courses(ACCOUNT)
    assert [(c.id, c.short_name, c.term) for c in courses] == [
        (1400003, "CSE 410", "Autumn 2026"),
        (1400001, "Math300 Au26", "Autumn 2026"),
        (1400002, "Discrete Structures", "Autumn 2026"),
    ]
    assert len(parse_courses(ACCOUNT, all_terms=True)) == 4


def test_parse_courses_skips_instructor_section_and_detects_logged_out():
    html = ACCOUNT.replace(
        '<h1 class="pageHeading">Your Courses</h1>',
        '<h1 class="pageHeading">Instructor Courses</h1><div class="courseList"><div class="courseList--term">Autumn 2026</div>'
        '<div class="courseList--coursesForTerm"><a class="courseBox" href="/courses/777"><h3 class="courseBox--shortname">CSE 190</h3></a></div></div>'
        '<h1 class="pageHeading">Student Courses</h1>',
    )
    assert 777 not in {c.id for c in parse_courses(html)}
    assert parse_courses("<html><form action='/login'></form></html>") is None


def test_parse_assignments():
    rows = {a.name: a for a in parse_assignments(COURSE)}
    assert list(rows) == ["PSet 1", "Lab 2 Part 1", "PSet 2", "Lab Partner Form", "Midterm", "Lab 2 Questions", "Quiz 1"]
    assert (rows["PSet 1"].id, rows["PSet 1"].status_text) == ("6100001", "18.5 / 20.0 1 Day, 3 Minutes Late")
    assert rows["PSet 2"].id == "6100003"  # from the submit button
    assert rows["Lab Partner Form"].id is None  # closed, never submitted: just text
    lab = rows["Lab 2 Part 1"]
    assert lab.due == datetime(2026, 10, 23, 18, 0, tzinfo=LA) and lab.late_due == datetime(2026, 10, 25, 18, 0, tzinfo=LA)
    assert rows["Midterm"].due is None


def test_fetch_items_statuses_and_scores():
    seen: list[httpx.Request] = []
    result = fetch(source(course_map={1400002: "CSE 311"}), handler=gradescope_server(seen))
    items = {i.title: i for i in result.items}

    assert all(r.headers["Cookie"] == "_gradescope_session=abc" for r in seen)
    pset1 = items["PSet 1"]  # graded, and late
    assert (pset1.status, pset1.score, pset1.points, pset1.source_id) == ("graded", 18.5, 20.0, "gradescope:6100001")
    assert pset1.url == "https://www.gradescope.com/courses/1400003/assignments/6100001/submissions/9900001"
    assert pset1.raw["late"] is True
    questions = items["Lab 2 Questions"]  # unsubmitted, as a link to the upload form
    assert (questions.status, questions.source_id) == ("pending", "gradescope:6100006")
    assert questions.url == "https://www.gradescope.com/courses/1400003/assignments/6100006"
    quiz = items["Quiz 1"]  # in-class: graded, no dates
    assert (quiz.kind, quiz.status, quiz.score, quiz.due_at) == ("quiz", "graded", 4.0, None)
    assert items["Lab 2 Part 1"].status == "submitted"
    assert items["PSet 2"].status == "pending"  # due after "now" in the tests (Oct 23 noon)
    assert items["Lab Partner Form"].status == "missing"
    assert items["Lab Partner Form"].source_id.startswith("gradescope:1400003:")
    midterm = items["Midterm"]
    assert (midterm.kind, midterm.status, midterm.score, midterm.due_at) == ("exam", "graded", 41.0, None)

    codes = [(c.code, c.links["gradescope"]) for c in result.courses]
    assert codes == [
        ("CSE 410", "https://www.gradescope.com/courses/1400003"),
        ("MATH 300", "https://www.gradescope.com/courses/1400001"),
        ("CSE 311", "https://www.gradescope.com/courses/1400002"),  # from course_map
    ]
    assert result.window is None


def test_expired_session():
    login = lambda r: httpx.Response(302, headers={"Location": "https://www.gradescope.com/login"})  # noqa: E731
    with pytest.raises(SourceError, match="fresh cookie"):
        fetch(source(), handler=login)
    logged_out = lambda r: httpx.Response(200, text="<html><form action='/login'></form></html>")  # noqa: E731
    with pytest.raises(SourceError, match="expired"):
        fetch(source(), handler=logged_out)


@pytest.mark.parametrize(
    ("text", "code"),
    [("Math300 Au26", "MATH 300"), ("CSE 410", "CSE 410"), ("e e 215", "E E 215"), ("Discrete Structures", None)],
)
def test_course_codes_from_gradescope_short_names(text, code):
    assert parse_course_code(text) == code
