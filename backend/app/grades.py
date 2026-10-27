"""Grade estimates: graded work sorted into the categories config.yaml lists, weighted as the
syllabus says. Only categories with grades so far count, and the result says how much of the
course grade that covers. A course without weights gets a plain points total."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from pydantic import BaseModel

from .config import CourseConfig, GradeCategory
from .coursecode import course_slug
from .models import Course
from .views import CourseOut, ItemOut


class GradedItem(BaseModel):
    id: int
    title: str
    url: str | None
    score: float
    points: float | None
    percent: float | None
    dropped: bool = False


class CategoryGrade(BaseModel):
    name: str
    weight: float
    percent: float | None  # None until something in it is graded
    earned: float
    possible: float
    items: list[GradedItem]


class CourseGrades(BaseModel):
    course: CourseOut
    weighted: bool  # whether config.yaml gives this course's weights
    estimate: float | None  # percent
    counted_weight: float  # how much of the course grade the estimate rests on, in percent
    categories: list[CategoryGrade]
    uncategorized: list[GradedItem]  # graded work that matched no category (not in the estimate)
    earned: float  # plain totals over all graded work
    possible: float


_PLURALS = [("zzes", "z"), ("ies", "y"), ("sses", "ss"), ("shes", "sh"), ("ches", "ch"), ("xes", "x"), ("s", "")]


def _singular(word: str) -> str:
    for plural, single in _PLURALS:
        if word.endswith(plural) and len(word) > len(plural) + 1:
            return word[: -len(plural)] + single
    return word


def _pattern(cat: GradeCategory) -> re.Pattern[str]:
    """The category's own regex, or its name in singular or plural: "Quizzes" matches "Quiz 2"."""
    if cat.match:
        return re.compile(cat.match, re.I)
    words = cat.name.lower().split()
    singular = " ".join([*words[:-1], _singular(words[-1])])
    return re.compile(rf"\b{re.escape(singular)}(?:e?s)?\b|\b{re.escape(cat.name.lower())}\b", re.I)


def _graded(item: ItemOut) -> GradedItem:
    percent = item.score / item.points * 100 if item.points else None
    return GradedItem(id=item.id, title=item.title, url=item.url, score=item.score, points=item.points, percent=percent)  # type: ignore[arg-type]


def _category(cat: GradeCategory, items: list[GradedItem]) -> CategoryGrade:
    ranked = sorted((i for i in items if i.percent is not None), key=lambda i: i.percent)  # type: ignore[arg-type, return-value]
    if 0 < cat.drop_lowest < len(ranked):
        for i in ranked[: cat.drop_lowest]:
            i.dropped = True
    kept = [i for i in items if not i.dropped]
    earned = sum(i.score for i in kept)
    possible = sum(i.points or 0 for i in kept)
    return CategoryGrade(
        name=cat.name,
        weight=cat.weight,
        percent=earned / possible * 100 if possible else None,
        earned=earned,
        possible=possible,
        items=items,
    )


def grade_course(course: Course, items: Iterable[ItemOut], config: CourseConfig | None) -> CourseGrades:
    graded = [i for i in items if i.score is not None]
    earned = sum(i.score for i in graded)  # type: ignore[misc]
    possible = sum(i.points or 0 for i in graded)
    cats = config.grading.categories if config and config.grading else []

    buckets: dict[str, list[GradedItem]] = {c.name: [] for c in cats}
    uncategorized: list[GradedItem] = []
    patterns = [(c, _pattern(c)) for c in cats]
    for item in graded:
        by_source = next((c for c in cats if item.category and c.name.lower() == item.category.lower()), None)
        by_title = next((c for c, p in patterns if p.search(item.title) or (item.category and p.search(item.category))), None)
        target = by_source or by_title
        (buckets[target.name] if target else uncategorized).append(_graded(item))

    categories = [_category(c, buckets[c.name]) for c in cats]
    total_weight = sum(c.weight for c in cats)
    counted = [c for c in categories if c.percent is not None]
    counted_weight = sum(c.weight for c in counted)
    if cats:
        estimate = sum(c.weight * c.percent for c in counted) / counted_weight if counted_weight else None  # type: ignore[operator]
    else:
        estimate = earned / possible * 100 if possible else None
        uncategorized = [_graded(i) for i in graded]
    return CourseGrades(
        course=CourseOut.model_validate(course),
        weighted=bool(cats),
        estimate=estimate,
        counted_weight=counted_weight / total_weight * 100 if total_weight else (100.0 if possible else 0.0),
        categories=categories,
        uncategorized=uncategorized,
        earned=earned,
        possible=possible,
    )


def course_grades(items: list[ItemOut], courses: Mapping[str, Course], configs: list[CourseConfig]) -> list[CourseGrades]:
    """Every course with graded work or configured weights."""
    by_course: dict[str, list[ItemOut]] = {}
    for item in items:
        if item.course:
            by_course.setdefault(item.course.id, []).append(item)
    config_by_id = {course_slug(c.code): c for c in configs}
    wanted = {cid for cid, its in by_course.items() if any(i.score is not None for i in its)}
    wanted |= {cid for cid, c in config_by_id.items() if c.grading and cid in courses}
    out = [grade_course(courses[cid], by_course.get(cid, []), config_by_id.get(cid)) for cid in wanted]
    return sorted(out, key=lambda g: g.course.code)
