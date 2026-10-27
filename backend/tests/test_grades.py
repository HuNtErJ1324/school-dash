from __future__ import annotations

import pytest

from app.config import CourseConfig, GradeCategory, Grading
from app.grades import grade_course
from app.models import Course
from app.views import CourseOut, ItemOut

COURSE = Course(id="cse410", code="CSE 410", name=None, color="#2f62b5", links={})


def graded(title: str, score: float | None, points: float, category: str | None = None, n: list[int] = [0]) -> ItemOut:  # noqa: B006
    n[0] += 1
    return ItemOut(
        id=n[0], source="gradescope", source_id=f"gs:{n[0]}", course=CourseOut.model_validate(COURSE), kind="assignment",
        title=title, url=None, at=None, due_at=None, starts_at=None, ends_at=None, all_day=False, location=None,
        status="graded" if score is not None else "pending", points=points, score=score, category=category,
        sources=[], done=False, hidden=False, pinned=False, note=None, separate=False,
    )  # fmt: skip


CONFIG = CourseConfig(
    code="CSE 410",
    grading=Grading(categories=[
        GradeCategory(name="Labs", weight=50),
        GradeCategory(name="Problem sets", weight=20, match=r"pset|problem set", drop_lowest=1),
        GradeCategory(name="Midterm", weight=10),
        GradeCategory(name="Final", weight=20, match=r"final exam"),
    ]),
)  # fmt: skip


def test_weighted_estimate_with_drops_and_partial_weight():
    items = [
        graded("Lab 1", 45, 50),  # 90%
        graded("Lab 2 Part 1", 40, 50),  # 80%
        graded("PSet 1", 10, 20),  # 50%: dropped as the lowest
        graded("PSet 2", 19, 20),  # 95%
        graded("Midterm", 41, 50),  # 82%
        graded("Reading quiz", 5, 5),  # matches nothing
        graded("PSet 3", None, 20),  # not graded yet: ignored
    ]
    g = grade_course(COURSE, items, CONFIG)
    cats = {c.name: c for c in g.categories}

    assert cats["Labs"].percent == pytest.approx(85.0)
    assert cats["Problem sets"].percent == pytest.approx(95.0)
    assert [i.dropped for i in cats["Problem sets"].items] == [True, False]
    assert cats["Final"].percent is None
    # (50*85 + 20*95 + 10*82) / 80
    assert g.estimate == pytest.approx((50 * 85 + 20 * 95 + 10 * 82) / 80)
    assert g.counted_weight == pytest.approx(80.0)
    assert [i.title for i in g.uncategorized] == ["Reading quiz"]
    assert (g.earned, g.possible) == (160, 195)


def test_source_category_beats_title_match():
    items = [graded("Week 3 check-in", 9, 10, category="Labs"), graded("Lab report", 5, 10, category="Midterm")]
    cats = {c.name: [i.title for i in c.items] for c in grade_course(COURSE, items, CONFIG).categories}
    assert cats["Labs"] == ["Week 3 check-in"] and cats["Midterm"] == ["Lab report"]


@pytest.mark.parametrize(
    ("category", "title"),
    [("Quizzes", "Quiz 2"), ("Labs", "Lab 1"), ("Problem sets", "Problem set 3"), ("Activities", "Activity 4"),
     ("Exercises", "Exercise 2"), ("Midterm", "Midterm"), ("Homework", "Homework 5")],
)  # fmt: skip
def test_category_names_match_singular_titles(category, title):
    config = CourseConfig(code="CSE 410", grading=Grading(categories=[GradeCategory(name=category, weight=100)]))
    g = grade_course(COURSE, [graded(title, 1, 1)], config)
    assert g.uncategorized == [] and g.categories[0].items[0].title == title


def test_unweighted_course_gets_a_points_total():
    g = grade_course(COURSE, [graded("HW1", 18, 20), graded("HW2", 9, 10)], None)
    assert (g.weighted, g.estimate, g.counted_weight) == (False, pytest.approx(90.0), 100.0)
    assert len(g.uncategorized) == 2 and g.categories == []


def test_nothing_graded_yet():
    g = grade_course(COURSE, [graded("Lab 1", None, 50)], CONFIG)
    assert (g.estimate, g.counted_weight) == (None, 0.0)
