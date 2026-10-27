from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Overlay
from app.sources.base import CourseRef, FetchResult, ItemIn
from app.sync import store_result

# Two sources that see the same things, the way canvas and canvas_feed do.
CONFIG = """
courses:
  - code: DEMO 101
    grading:
      categories:
        - name: Problem sets
          weight: 60
          match: problem set
        - name: Reading
          weight: 40
          match: reading
sources:
  - type: demo
  - type: demo
    name: demo2
"""


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        c.post("/api/sync")
        yield c


def by_title(client: TestClient, **params) -> dict[str, dict]:
    return {i["title"]: i for i in client.get("/api/items", params=params).json()}


def test_items_seen_by_two_sources_come_back_once(client):
    items = client.get("/api/items").json()
    assert len(items) == 17  # not 34
    ps3 = by_title(client)["Problem set 3"]
    assert sorted(s["source"] for s in ps3["sources"]) == ["demo", "demo2"]  # the two sync concurrently


def test_overlay_changes_reach_every_copy_and_separate_splits(client):
    ps3 = by_title(client)["Problem set 3"]
    client.patch(f"/api/items/{ps3['id']}/overlay", json={"done": True})
    with client.app.state.Session() as s:
        assert [o.done for o in s.scalars(select(Overlay))] == [True, True]
    assert by_title(client)["Problem set 3"]["done"] is True

    split = client.patch(f"/api/items/{ps3['id']}/overlay", json={"separate": True}).json()
    assert split["separate"] is True and len(split["sources"]) == 1
    copies = [i for i in client.get("/api/items").json() if i["title"] == "Problem set 3"]
    assert len(copies) == 2 and all(c["done"] for c in copies)

    # Undoing from either copy brings them back together.
    rejoined = client.patch(f"/api/items/{copies[1]['id']}/overlay", json={"separate": False}).json()
    assert len(rejoined["sources"]) == 2 and rejoined["separate"] is False
    assert len([i for i in client.get("/api/items").json() if i["title"] == "Problem set 3"]) == 1


def test_merged_status_wins_over_a_source_that_knows_less(client):
    now = datetime.now(timezone.utc)
    graded = ItemIn(source_id="ps3", kind="assignment", title="Problem set 3", course=CourseRef(code="DEMO 101"),
                    due_at=now + timedelta(hours=5), status="graded", points=20, score=18)  # fmt: skip
    store_result(client.app.state.Session, "demo2", FetchResult(items=[graded]), now)
    ps3 = by_title(client)["Problem set 3"]
    assert (ps3["status"], ps3["score"], ps3["points"]) == ("graded", 18, 20)
    assert ps3["status"] in {i["status"] for i in client.get("/api/items", params={"status": "graded"}).json()}


def test_grades_endpoint(client):
    now = datetime.now(timezone.utc)
    scored = [
        ItemIn(source_id="ps3", kind="assignment", title="Problem set 3", course=CourseRef(code="DEMO 101"),
               due_at=now - timedelta(days=1), status="graded", points=20, score=18),
        ItemIn(source_id="rr4", kind="assignment", title="Reading response 4", course=CourseRef(code="DEMO 101"),
               due_at=now - timedelta(days=1), status="graded", points=5, score=4),
    ]  # fmt: skip
    store_result(client.app.state.Session, "demo2", FetchResult(items=scored), now)
    grades = {g["course"]["code"]: g for g in client.get("/api/grades").json()}

    demo101 = grades["DEMO 101"]
    assert demo101["weighted"] is True
    # Problem sets: the demo's PS1 18/20 and PS2 15.5/20, plus PS3 18/20 from demo2.
    # Reading: the demo's 5/5, plus 4/5 from demo2.
    assert demo101["estimate"] == pytest.approx(0.6 * (51.5 / 60 * 100) + 0.4 * (9 / 10 * 100))
    assert demo101["counted_weight"] == pytest.approx(100)
    demo202 = grades["DEMO 202"]  # graded work but no weights in this config: a plain total
    assert (demo202["weighted"], demo202["estimate"]) == (False, pytest.approx(53 / 60 * 100))
    assert "DEMO 303" not in grades  # nothing graded, no weights
