import pytest

from app.sources.classify import kind_from_title


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Midterm 1", "exam"),
        ("Final Exam", "exam"),
        ("Exam 2 (in class)", "exam"),
        ("Quiz 3", "quiz"),
        ("Reading quiz", "quiz"),
        ("Midterm review", "assignment"),
        ("Quiz 1 Corrections", "assignment"),
        ("Practice exam", "assignment"),
        ("Examples worksheet", "assignment"),
        ("Final project", "assignment"),
        ("HW1", "assignment"),
    ],
)
def test_kind_from_title(title, kind):
    assert kind_from_title(title) == kind
