from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from app.sources.base import SourceError
from app.sources.canvas import CanvasConfig, CanvasSource
from conftest import fetch

AUTUMN = {"name": "Autumn 2026", "start_at": "2025-09-30T08:00:00Z"}
COURSES = [
    {"id": 1700001, "name": "CSE 312 A Au 26: Foundations Of Computing II", "course_code": "CSE 312 A", "term": AUTUMN},
    {"id": 1700099, "access_restricted_by_date": True},
    # A university-wide training, in a term of its own (with a later start date).
    {"id": 990000000000000001, "name": "Campus Safety Training 2025-2026", "course_code": "Campus Safety Training 2025-2026",
     "term": {"name": "2025-2026 101", "start_at": "2026-03-30T07:00:00Z"}},
]  # fmt: skip
GROUPS = [{"id": 31, "name": "Problem sets", "group_weight": 60}, {"id": 32, "name": "Exams", "group_weight": 40}]
ASSIGNMENTS = [
    {
        "id": 9001, "name": "Homework 4", "due_at": "2026-10-29T06:59:00Z", "points_possible": 100.0,
        "assignment_group_id": 31, "submission_types": ["online_upload"],
        "html_url": "https://canvas.uw.edu/courses/1700001/assignments/9001",
        "submission": {"workflow_state": "submitted", "submitted_at": "2026-10-27T20:00:00Z", "score": None},
    },
    {
        "id": 9000, "name": "Homework 3", "due_at": "2026-09-30T06:59:00Z", "points_possible": 100.0,
        "assignment_group_id": 31, "submission_types": ["online_upload"],
        "submission": {"workflow_state": "graded", "submitted_at": "2026-09-29T20:00:00Z", "score": 92.5},
    },
    {
        "id": 9002, "name": "Concept check 3", "due_at": "2026-10-20T06:59:00Z", "points_possible": 5.0,
        "assignment_group_id": 31, "submission_types": ["online_quiz"], "quiz_id": 77,
        "submission": {"workflow_state": "unsubmitted", "missing": True},
    },
    {
        "id": 9003, "name": "Midterm", "due_at": "2026-11-06T19:30:00Z", "points_possible": 50.0,
        "assignment_group_id": 32, "submission_types": ["on_paper"],
        "submission": {"workflow_state": "unsubmitted"},
    },
]  # fmt: skip
PAGE_1 = [
    # Graded work: comes from the assignment list instead.
    {"plannable_type": "assignment", "plannable_id": 9001, "course_id": 1700001, "plannable_date": "2026-10-29T06:59:00Z",
     "plannable": {"title": "Homework 4", "due_at": "2026-10-29T06:59:00Z"}, "submissions": {"submitted": True}},
    {"plannable_type": "quiz", "plannable_id": 77, "course_id": 1700001, "plannable_date": "2026-10-20T06:59:00Z",
     "plannable": {"title": "Concept check 3"}, "submissions": {"missing": True}},
]  # fmt: skip
PAGE_2 = [
    {
        "plannable_type": "calendar_event", "plannable_id": 555, "course_id": 1700001,
        "html_url": "https://canvas.uw.edu/calendar?event_id=555", "plannable_date": "2026-10-27T17:00:00Z",
        "plannable": {"title": "Review session", "start_at": "2026-10-27T17:00:00Z", "end_at": "2026-10-27T18:00:00Z",
                      "all_day": False, "location_name": "MGH 389"},
        "submissions": False,
    },
    {
        "plannable_type": "announcement", "plannable_id": 31, "course_id": 1700001,
        "html_url": "/courses/1700001/discussion_topics/31", "plannable_date": "2026-10-22T16:00:00Z",
        "plannable": {"title": "Midterm logistics"}, "submissions": False,
    },
    {
        "plannable_type": "discussion_topic", "plannable_id": 40, "course_id": 1700001,
        "html_url": "/courses/1700001/discussion_topics/40", "plannable_date": "2026-10-30T06:59:00Z",
        "plannable": {"title": "Introduce yourself", "todo_date": "2026-10-30T06:59:00Z"}, "submissions": False,
    },
    {
        "plannable_type": "announcement", "plannable_id": 77, "course_id": 990000000000000001,
        "html_url": "/courses/990000000000000001/discussion_topics/77", "plannable_date": "2026-10-22T16:00:00Z",
        "plannable": {"title": "Training reminder"}, "submissions": False,
    },
]  # fmt: skip


def canvas_server(seen: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        headers = {}
        if path == "/api/v1/courses":
            assert request.url.params.get("include[]") == "term"
            body = COURSES
        elif path == "/api/v1/courses/1700001/assignment_groups":
            body = GROUPS
        elif path == "/api/v1/courses/1700001/assignments":
            assert request.url.params.get("include[]") == "submission"
            body = ASSIGNMENTS
        elif path == "/api/v1/planner/items":
            if request.url.params.get("page") == "2":
                body = PAGE_2
            else:
                body = PAGE_1
                headers["Link"] = '<https://canvas.uw.edu/api/v1/planner/items?page=2&per_page=100>; rel="next"'
        else:
            return httpx.Response(404)
        # Canvas guards cookie-authenticated JSON with this prefix.
        return httpx.Response(200, text="while(1);" + json.dumps(body), headers=headers)

    return handle


def test_assignments_with_scores_and_planner_extras():
    seen: list[httpx.Request] = []
    source = CanvasSource("canvas", CanvasConfig(cookie="canvas_session=abc123"))
    result = fetch(source, handler=canvas_server(seen))
    items = {i.source_id: i for i in result.items}

    assert all(r.headers["Cookie"] == "canvas_session=abc123" for r in seen)
    assert len([r for r in seen if r.url.path == "/api/v1/planner/items"]) == 2  # followed the Link header
    assert not any("1700099" in r.url.path for r in seen)  # no term: not this term's class
    assert not any("990000000000000001" in r.url.path for r in seen)  # the training: skipped

    hw4 = items["assignment:9001"]
    assert (hw4.status, hw4.points, hw4.score, hw4.category) == ("submitted", 100.0, None, "Problem sets")
    assert hw4.due_at == datetime(2026, 10, 29, 6, 59, tzinfo=timezone.utc)
    assert hw4.course.canvas_id == 1700001

    hw3 = items["assignment:9000"]  # outside the planner window, still here with its grade
    assert (hw3.status, hw3.score) == ("graded", 92.5)

    quiz = items["assignment:9002"]
    assert (quiz.kind, quiz.status) == ("quiz", "missing")
    midterm = items["assignment:9003"]
    assert (midterm.kind, midterm.status, midterm.category) == ("exam", None, "Exams")
    assert midterm.starts_at == midterm.due_at

    # The planner's copies of graded work are dropped; everything else stays.
    assert "assignment:9001" in items and "quiz:77" not in items
    assert items["calendar_event:555"].location == "MGH 389"
    assert items["announcement:31"].kind == "announcement"
    assert items["discussion_topic:40"].kind == "discussion"

    assert "announcement:77" not in items  # the training's planner items go too
    assert [(c.canvas_id, c.code) for c in result.courses] == [(1700001, "CSE 312")]
    assert result.courses[0].links == {"canvas": "https://canvas.uw.edu/courses/1700001"}


def test_current_term_tie_goes_to_the_academic_term():
    from app.sources.canvas import current_term

    one_class = [COURSES[0], COURSES[2]]  # one class and one training: a tie
    assert [c["id"] for c in current_term(one_class, None)] == [1700001]
    assert [c["id"] for c in current_term(COURSES, ["2025-2026 101"])] == [990000000000000001]  # explicit terms win


def test_expired_session_is_a_readable_error():
    source = CanvasSource("canvas", CanvasConfig(cookie="canvas_session=old"))
    with pytest.raises(SourceError, match="fresh session cookie"):
        fetch(source, handler=lambda r: httpx.Response(302, headers={"Location": "https://idp.u.washington.edu/"}))


def test_config_needs_exactly_one_auth_method():
    with pytest.raises(ValidationError):
        CanvasConfig()
    with pytest.raises(ValidationError):
        CanvasConfig(token="t", cookie="c")
