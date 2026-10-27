from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import CourseConfig
from app.courses import sync_config_courses
from app.db import Base, make_engine, make_sessionmaker
from app.models import Course, Item
from app.sources.base import CourseRef, FetchResult, ItemIn
from app.sync import store_result

T0 = datetime(2026, 10, 23, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def Session(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    yield make_sessionmaker(engine)
    engine.dispose()


def deadline(sid: str, days: float, course: CourseRef | None = None) -> ItemIn:
    return ItemIn(source_id=sid, kind="assignment", title=sid.upper(), due_at=T0 + timedelta(days=days), course=course)


def rows(Session) -> dict[str, Item]:
    with Session() as s:
        return {i.source_id: i for i in s.scalars(select(Item))}


def test_upsert_then_remove_then_restore(Session):
    window = (T0 - timedelta(days=7), T0 + timedelta(days=30))
    store_result(Session, "src", FetchResult(items=[deadline("a", 1), deadline("b", 2)], window=window), T0)
    first_seen = rows(Session)["a"].first_seen_at

    t1 = T0 + timedelta(hours=1)
    store_result(Session, "src", FetchResult(items=[deadline("a", 1)], window=window), t1)
    r = rows(Session)
    assert r["b"].removed_at == t1 and r["a"].removed_at is None
    assert r["a"].first_seen_at == first_seen and r["a"].last_seen_at == t1

    t2 = T0 + timedelta(hours=2)
    store_result(Session, "src", FetchResult(items=[deadline("a", 1), deadline("b", 3)], window=window), t2)
    r = rows(Session)
    assert r["b"].removed_at is None and r["b"].at == T0 + timedelta(days=3)
    assert len(r) == 2


def test_items_outside_the_window_are_not_removed(Session):
    store_result(Session, "src", FetchResult(items=[deadline("old", -20), deadline("new", 1)]), T0)
    narrow = (T0 - timedelta(days=7), T0 + timedelta(days=30))
    store_result(Session, "src", FetchResult(items=[deadline("new", 1)], window=narrow), T0 + timedelta(hours=1))
    assert rows(Session)["old"].removed_at is None


def test_other_sources_are_untouched(Session):
    store_result(Session, "one", FetchResult(items=[deadline("a", 1)]), T0)
    store_result(Session, "two", FetchResult(items=[]), T0 + timedelta(hours=1))
    assert rows(Session)["a"].removed_at is None


def test_courses_match_config_by_code_and_learn_canvas_id(Session):
    sync_config_courses(Session, [CourseConfig(code="CSE 410", color="#4b2e83")])
    ref = CourseRef(code="CSE 410", canvas_id=1700410, name="CSE 410 A Au 26: Computer Systems")
    store_result(Session, "src", FetchResult(items=[deadline("a", 1, ref)], courses=[ref]), T0)
    # A later item that only knows the Canvas id lands in the same course.
    store_result(Session, "src2", FetchResult(items=[deadline("b", 1, CourseRef(canvas_id=1700410))]), T0)

    with Session() as s:
        courses = s.scalars(select(Course)).all()
    assert [(c.id, c.color, c.canvas_id) for c in courses] == [("cse410", "#4b2e83", 1700410)]
    assert {i.course_id for i in rows(Session).values()} == {"cse410"}


def colors(Session) -> dict[str, str | None]:
    with Session() as s:
        return {c.id: c.color for c in s.scalars(select(Course))}


def test_new_courses_get_distinct_colors_that_stick(Session):
    sync_config_courses(Session, [CourseConfig(code="CSE 410", color="#2f6fd0")])
    refs = [CourseRef(code="MATH 300"), CourseRef(code="MATH 208")]
    store_result(Session, "src", FetchResult(items=[deadline(f"i{n}", 1, r) for n, r in enumerate(refs)]), T0)

    first = colors(Session)
    assert first["cse410"] == "#2f6fd0"
    assert first["math208"] == "#d9622b" and first["math300"] == "#0f9a75"  # next pencils, in order
    assert len(set(first.values())) == 3  # nobody shares CSE 410's blue

    store_result(Session, "src", FetchResult(items=[deadline("z", 1, CourseRef(code="CHEM 142"))]), T0)
    sync_config_courses(Session, [CourseConfig(code="CSE 410", color="#2f6fd0")])  # a restart
    after = colors(Session)
    assert {k: after[k] for k in first} == first and len(set(after.values())) == 4


def test_current_courses_get_the_safe_pencils(Session):
    from app.courses import PENCILS, assign_colors

    # Last quarter's courses hold the first three pencils...
    for n, code in enumerate(["CSE 143", "CSE 333", "CSE 344"]):
        store_result(Session, "old", FetchResult(items=[deadline(f"o{n}", -90, CourseRef(code=code))]), T0)
    # ...and this quarter's start out on later ones.
    store_result(Session, "live", FetchResult(items=[deadline("a", 1, CourseRef(code="MATH 208"))]), T0, ["live"])
    with Session.begin() as s:
        s.get(Course, "math208").color = PENCILS[5]
    sync_config_courses(Session, [CourseConfig(code="CSE 410", color="#123456")], ["live"])  # a custom, locked color

    c = colors(Session)
    assert c["math208"] == PENCILS[0]  # moved onto a safe pencil the old courses no longer hold
    assert c["cse410"] == "#123456"  # config colors never move
    assert c["cse143"] == PENCILS[0]  # stale courses keep theirs; they just don't count

    # Once on a safe pencil, a course keeps it.
    store_result(Session, "live", FetchResult(items=[deadline("a", 1, CourseRef(code="MATH 208")),
                                                     deadline("b", 1, CourseRef(code="MATH 126"))]), T0, ["live"])  # fmt: skip
    with Session.begin() as s:
        assign_colors(s, ["live"])
    c = colors(Session)
    assert (c["math208"], c["math126"]) == (PENCILS[0], PENCILS[1])


def test_courses_on_the_retired_palette_are_recolored(Session):
    store_result(Session, "src", FetchResult(items=[deadline("a", 1, CourseRef(code="MATH 300"))]), T0)
    with Session.begin() as s:
        s.get(Course, "math300").color = "#5f8a1c"  # an old pencil
    sync_config_courses(Session, [])  # what a restart does
    assert colors(Session)["math300"] == "#2f6fd0"


def test_course_links_from_sources_fill_in_but_config_wins(Session):
    sync_config_courses(Session, [CourseConfig(code="CSE 410", links={"site": "https://cse410.example"})])
    ref = CourseRef(code="CSE 410", links={"canvas": "https://canvas.example/courses/1", "site": "https://other"})
    store_result(Session, "src", FetchResult(items=[deadline("a", 1, ref)]), T0)
    sync_config_courses(Session, [CourseConfig(code="CSE 410", links={"site": "https://cse410.example"})])  # restart

    with Session() as s:
        links = s.get(Course, "cse410").links
    assert links == {"site": "https://cse410.example", "canvas": "https://canvas.example/courses/1"}


def test_unknown_courses_are_created_from_names(Session):
    ref = CourseRef(canvas_id=42, name="MATH 126 B Au 26: Calculus With Analytic Geometry III")
    store_result(Session, "src", FetchResult(items=[deadline("a", 1, ref)]), T0)
    with Session() as s:
        course = s.get(Course, "math126")
    assert course is not None and (course.code, course.canvas_id) == ("MATH 126", 42)
