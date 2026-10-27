"""Guessing what an item is from its title, for sources that file everything as an "assignment"."""

from __future__ import annotations

import re

from .base import Kind

_EXAM = re.compile(r"\b(midterm|exam)s?\b", re.I)
_QUIZ = re.compile(r"\bquiz(zes)?\b", re.I)
# Work *about* an exam or quiz is still an assignment.
_ABOUT_IT = re.compile(
    r"\b(corrections?|review|reflections?|retakes?|practice|prep|study guide|solutions?|regrades?)\b", re.I
)


def kind_from_title(title: str, default: Kind = "assignment") -> Kind:
    """Canvas files exams and in-class quizzes as plain assignments; their titles say what they are."""
    if _ABOUT_IT.search(title):
        return default
    if _EXAM.search(title):
        return "exam"
    if _QUIZ.search(title):
        return "quiz"
    return default
