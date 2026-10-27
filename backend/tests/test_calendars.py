from __future__ import annotations

import shutil
from datetime import datetime, timezone

import httpx

from app.sources.canvas_feed import CanvasFeedConfig, CanvasFeedSource
from app.sources.cse_calendar import CseCalendarConfig, CseCalendarSource
from app.sources.ics import IcsConfig, IcsSource
from conftest import FIXTURES, LA, fetch


def by_id(result):
    return {i.source_id: i for i in result.items}


def cse410(base_url) -> CseCalendarSource:
    cfg = CseCalendarConfig(course="CSE 410", base_url=str(base_url), calendars=["lecture-A", "oh"])
    return CseCalendarSource("cse410", cfg)


# --- generic ics -------------------------------------------------------------


def test_ics_expands_repeats_and_keys_moved_instance_on_recurrence_id():
    items = by_id(fetch(IcsSource("sections", IcsConfig(url=str(FIXTURES / "generic.ics"), kind="section"))))

    weekly = sorted(k for k in items if k.startswith("weekly-1"))
    assert weekly == [
        "weekly-1@example.com/20261019T1700Z",
        "weekly-1@example.com/20261026T1700Z",  # moved to the 27th, still keyed on the original slot
        "weekly-1@example.com/20261102T1800Z",  # after DST ends: 10am PST
        "weekly-1@example.com/20261109T1800Z",
    ]
    moved = items["weekly-1@example.com/20261026T1700Z"]
    assert moved.title == "Quiz section (moved)"
    assert moved.starts_at == datetime(2026, 10, 27, 10, 0, tzinfo=LA)
    assert moved.kind == "section" and moved.location == "MGH 231"


def test_ics_floating_times_use_configured_timezone():
    items = by_id(fetch(IcsSource("x", IcsConfig(url=str(FIXTURES / "generic.ics")))))
    floating = items["floating-1@example.com"]
    assert floating.starts_at.astimezone(timezone.utc) == datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)


# --- UW CSE createcal --------------------------------------------------------


def test_cse_calendar_deadlines():
    items = by_id(fetch(cse410(FIXTURES / "cse410")))

    pset = items["due:pset-2"]
    assert (pset.kind, pset.title, pset.all_day) == ("assignment", "PSet 2", True)
    assert pset.due_at == datetime(2026, 10, 28, 23, 59, tzinfo=LA)
    assert pset.course.code == "CSE 410"

    dd = items["due:lab-2-part-1-dd"]  # "(due 6pm PDT)" in the title sets the time
    assert (dd.title, dd.all_day) == ("Lab 2 Part 1 DD", False)
    assert dd.due_at == datetime(2026, 10, 30, 18, 0, tzinfo=LA)

    assert "due:old-thing" not in items  # before the lookback window


def test_cse_calendar_events():
    items = list(fetch(cse410(FIXTURES / "cse410")).items)
    kinds = {i.title: i.kind for i in items}
    assert kinds["Lab 3 Release"] == "release"
    assert kinds["Midterm"] == "exam"

    lecture = next(i for i in items if i.kind == "lecture")
    assert lecture.title == "Lecture: Scheduling"
    assert lecture.starts_at == datetime(2026, 10, 26, 11, 30, tzinfo=LA)
    assert lecture.location == "CSE2 G01"

    oh = [i for i in items if i.kind == "office_hours"]
    assert {i.title for i in oh} == {"OH (Riley)"}
    days = [i.starts_at.date().isoformat() for i in oh[:3]]
    assert days == ["2026-10-19", "2026-11-02", "2026-11-09"]  # the 26th is an EXDATE
    assert all(i.starts_at.hour == 13 and i.starts_at.minute == 30 for i in oh)  # across DST


def test_all_day_deadline_at_the_window_edge_is_still_fetched():
    # At 9pm on Oct 23 the 7-day window starts 9pm Oct 16 Pacific (4am Oct 17 UTC). "PSet 1"
    # is all day on Oct 16 and due 11:59pm that night, inside the window. The calendar library
    # judges all-day dates in the window's timezone, and the server's window is in UTC, where
    # Oct 16 ended at 5pm Pacific; so without padding the deadline silently went missing.
    ics = (FIXTURES / "cse410" / "calendar-lecture-A.ics").read_text().replace(
        "DTEND;VALUE=DATE:20261029\nDTSTART;VALUE=DATE:20261028\nSUMMARY:410 PSet 2 Due",
        "DTEND;VALUE=DATE:20261017\nDTSTART;VALUE=DATE:20261016\nSUMMARY:410 PSet 1 Due",
    )
    assert "PSet 1 Due" in ics

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ics if "lecture" in request.url.path else "BEGIN:VCALENDAR\nEND:VCALENDAR\n")

    now = datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)  # as the server computes it
    items = by_id(fetch(cse410("https://courses.example/cal/"), now=now, handler=serve))
    assert items["due:pset-1"].due_at == datetime(2026, 10, 16, 23, 59, tzinfo=LA)


def test_cse_calendar_ids_survive_regenerated_uids(tmp_path):
    first = set(by_id(fetch(cse410(FIXTURES / "cse410"))))

    for f in (FIXTURES / "cse410").glob("*.ics"):
        shutil.copy(f, tmp_path / f.name)
        p = tmp_path / f.name
        p.write_text(p.read_text().replace("202610231329521792787392", "202610241000000000000000"))
    second = set(by_id(fetch(cse410(tmp_path))))

    assert first == second


# --- Canvas calendar feed ----------------------------------------------------

FEED_URL = "https://canvas.uw.edu/feeds/calendars/user_SECRET.ics"


def canvas_feed_server(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == FEED_URL
    return httpx.Response(200, text=(FIXTURES / "canvas-feed.ics").read_text())


def test_canvas_feed_assignments_and_courses():
    result = fetch(CanvasFeedSource("canvas-feed", CanvasFeedConfig(url=FEED_URL)), handler=canvas_feed_server)
    items = by_id(result)

    hw = items["assignment:9001"]
    assert (hw.kind, hw.title, hw.all_day) == ("assignment", "Homework 4", False)
    assert hw.due_at == datetime(2026, 10, 28, 21, 45, tzinfo=LA)
    assert hw.url == "https://canvas.uw.edu/courses/1700001/assignments/9001"
    # The usual short tag gives a code but no name.
    assert (hw.course.code, hw.course.canvas_id, hw.course.name) == ("MATH 300", 1700001, None)
    assert hw.course.links == {"canvas": "https://canvas.uw.edu/courses/1700001"}

    reading = items["assignment:9005"]  # all-day: gets the default due time
    assert reading.all_day and reading.due_at == datetime(2026, 10, 30, 23, 59, tzinfo=LA)
    assert reading.course.code == "E E 215"
    assert reading.course.name == "E E 215 A Au 26: Fundamentals Of Electrical Engineering"

    review = items["calendar_event:555"]
    assert (review.kind, review.title, review.location) == ("event", "Review session", "MGH 389")
    assert review.starts_at == datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc)

    assert sorted(c.canvas_id for c in result.courses) == [1700001, 1700002, 1700003]


def test_canvas_feed_recognises_exams_and_quizzes_by_title():
    items = by_id(fetch(CanvasFeedSource("canvas-feed", CanvasFeedConfig(url=FEED_URL)), handler=canvas_feed_server))

    midterm = items["assignment:9002"]
    assert midterm.kind == "exam"
    assert midterm.starts_at == midterm.due_at == datetime(2026, 10, 26, 12, 30, tzinfo=LA)  # on the day's agenda too

    assert items["assignment:9003"].kind == "quiz"
    assert items["assignment:9003"].starts_at is None
    assert items["assignment:9004"].kind == "assignment"  # "Quiz 1 Corrections" is work about a quiz


def test_feed_errors_hide_the_secret_url():
    import pytest

    from app.sources.base import SourceError

    source = CanvasFeedSource("canvas-feed", CanvasFeedConfig(url=FEED_URL))
    with pytest.raises(SourceError) as e:
        fetch(source, handler=lambda r: httpx.Response(404))
    assert "SECRET" not in str(e.value) and "404" in str(e.value)
