from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.dedupe import regroup, same_work
from app.db import Base, make_engine, make_sessionmaker
from app.migrate import upgrade
from app.models import Item, Overlay
from app.sources.base import CourseRef, FetchResult, ItemIn
from app.sync import store_result
from conftest import LA

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=LA)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("HW3", "Homework 3", True),
        ("Homework 3", "Homework 3: Graph search", True),
        ("Problem Set 4", "PSet 4", True),
        ("PS 4", "Problem set 4", True),
        ("Lab 2 Part 1 DD", "Lab 2 Part 1 Design Doc", True),
        ("Lab 2 Part 2 DD Due (due 6pm PDT)", "Lab 2 Part 2 DD", True),
        ("Midterm Exam", "Midterm", True),
        ("Lab Partner Form", "lab partner form", True),
        ("Lab 2 Part 1", "Lab 2 Part 2", False),
        ("Quiz 2", "Quiz 3", False),
        ("Final Exam", "Final project", False),
        ("Homework 3", "Reading 3", False),
    ],
)
def test_same_work(a, b, same):
    assert same_work(a, b) is same
    assert same_work(b, a) is same


@pytest.fixture
def Session(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    upgrade(engine)
    yield make_sessionmaker(engine)
    engine.dispose()


def deadline(sid: str, title: str, course: str, due: datetime, all_day: bool = False) -> ItemIn:
    return ItemIn(source_id=sid, kind="assignment", title=title, course=CourseRef(code=course), due_at=due, all_day=all_day)


def groups(Session) -> list[set[tuple[str, str]]]:
    with Session() as s:
        by_group: dict[int, set] = {}
        for i in s.scalars(select(Item)):
            by_group.setdefault(i.group_id, set()).add((i.source, i.title))
    return sorted(by_group.values(), key=lambda g: sorted(g))


def test_grouping_across_canvas_gradescope_and_course_site(Session):
    math, cse = "MATH 300", "CSE 410"
    hw3_due = T0.replace(hour=21, minute=45)
    store_result(Session, "canvas_feed", FetchResult(items=[deadline("assignment:100", "Homework 3", math, hw3_due)]), T0)
    store_result(Session, "canvas", FetchResult(items=[deadline("assignment:100", "Homework 3", math, hw3_due)]), T0)
    store_result(Session, "cse410", FetchResult(items=[
        deadline("due:lab-2-part-1", "Lab 2 Part 1", cse, T0.replace(hour=23, minute=59), all_day=True),
        deadline("due:lab-2-part-1-dd", "Lab 2 Part 1 DD", cse, (T0 - timedelta(days=7)).replace(hour=23, minute=59), all_day=True),
    ]), T0)  # fmt: skip
    store_result(Session, "cse333", FetchResult(items=[
        deadline("due:lab-2-part-1", "Lab 2 Part 1", "CSE 333", T0.replace(hour=23, minute=59), all_day=True),
    ]), T0)  # fmt: skip
    store_result(Session, "gradescope", FetchResult(items=[
        deadline("gradescope:5", "HW 3", math, hw3_due),
        deadline("gradescope:6", "Lab 2 Part 1", cse, T0.replace(hour=18)),
        deadline("gradescope:7", "Lab 2 Part 1 DD", cse, (T0 - timedelta(days=7)).replace(hour=18)),
        deadline("gradescope:8", "Homework 3", math, hw3_due),  # a second Gradescope copy: can't join too
    ]), T0)  # fmt: skip

    assert groups(Session) == [
        {("canvas", "Homework 3"), ("canvas_feed", "Homework 3"), ("gradescope", "HW 3")},
        {("cse333", "Lab 2 Part 1")},  # same id as CSE 410's, different course
        {("cse410", "Lab 2 Part 1"), ("gradescope", "Lab 2 Part 1")},
        {("cse410", "Lab 2 Part 1 DD"), ("gradescope", "Lab 2 Part 1 DD")},  # a week apart from Lab 2 Part 1
        {("gradescope", "Homework 3")},
    ]
    with Session() as s:  # a group's id is its lowest member's id
        for i in s.scalars(select(Item)):
            assert i.group_id == min(m.id for m in s.scalars(select(Item).where(Item.group_id == i.group_id)))


def test_exact_titles_pair_before_looser_ones(Session):
    due = T0.replace(hour=23, minute=59)
    # The questions come first in id order, so a first-come rule would pair them with the lab.
    store_result(Session, "gradescope", FetchResult(items=[
        deadline("gradescope:2", "Lab 1 Questions", "CSE 410", due),
        deadline("gradescope:1", "Lab 1", "CSE 410", due),
    ]), T0)  # fmt: skip
    store_result(Session, "cse410", FetchResult(items=[deadline("due:lab-1", "Lab 1", "CSE 410", due, all_day=True)]), T0)
    assert groups(Session) == [
        {("cse410", "Lab 1"), ("gradescope", "Lab 1")},
        {("gradescope", "Lab 1 Questions")},
    ]


def test_undated_graded_work_joins_its_one_dated_match(Session):
    quiz = ItemIn(source_id="gradescope:9", kind="quiz", title="Quiz 1", course=CourseRef(code="MATH 300"), score=4, points=10)
    store_result(Session, "gradescope", FetchResult(items=[quiz]), T0)
    store_result(Session, "canvas_feed", FetchResult(items=[deadline("assignment:5", "Quiz 1", "MATH 300", T0)]), T0)
    assert groups(Session) == [{("canvas_feed", "Quiz 1"), ("gradescope", "Quiz 1")}]


def test_undated_work_stays_alone_when_the_match_is_ambiguous(Session):
    exam = ItemIn(source_id="gradescope:9", kind="exam", title="Midterm", course=CourseRef(code="CSE 410"))
    store_result(Session, "gradescope", FetchResult(items=[exam]), T0)
    store_result(Session, "cse410", FetchResult(items=[
        deadline("midterm/a", "Midterm", "CSE 410", T0),
        deadline("midterm/b", "Midterm", "CSE 410", T0 + timedelta(days=30)),  # two midterms called "Midterm"
    ]), T0)  # fmt: skip
    assert len(groups(Session)) == 3


def test_keep_separate(Session):
    due = T0.replace(hour=18)
    store_result(Session, "cse410", FetchResult(items=[deadline("due:pset-2", "PSet 2", "CSE 410", due)]), T0)
    store_result(Session, "gradescope", FetchResult(items=[deadline("gradescope:9", "Problem Set 2", "CSE 410", due)]), T0)
    assert len(groups(Session)) == 1

    with Session.begin() as s:
        gs = s.scalar(select(Item).where(Item.source == "gradescope"))
        s.add(Overlay(item_id=gs.id, separate=True, updated_at=T0))
    with Session.begin() as s:
        regroup(s)
    assert len(groups(Session)) == 2


def test_upgrade_adds_new_columns_to_an_old_database(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:  # make it look like a phase-2 database
        conn.execute(text("DROP INDEX ix_items_group_id"))
        for col in ("score", "category", "group_id"):
            conn.execute(text(f"ALTER TABLE items DROP COLUMN {col}"))
        conn.execute(text("ALTER TABLE overlays DROP COLUMN separate"))
        conn.execute(text(
            "INSERT INTO items (source, source_id, kind, title, all_day, raw, first_seen_at, last_seen_at) "
            "VALUES ('x', '1', 'assignment', 'Kept', 0, '{}', '2026-10-01', '2026-10-01')"
        ))  # fmt: skip
        conn.execute(text("INSERT INTO overlays (item_id, done, hidden, pinned, updated_at) VALUES (1, 1, 0, 0, '2026-10-01')"))

    upgrade(engine)
    upgrade(engine)  # and again: a no-op
    Session = make_sessionmaker(engine)
    with Session() as s:
        item = s.get(Item, 1)
        assert (item.title, item.score, item.group_id) == ("Kept", None, None)
        assert (s.get(Overlay, 1).done, s.get(Overlay, 1).separate) == (True, False)
    engine.dispose()
