from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sources.base import CourseRef, FetchResult, ItemIn
from app.sync import store_result
from conftest import FIXTURES

CONFIG = f"""
quarter: 26au
courses:
  - code: CSE 410
    color: "#4b2e83"
    links:
      site: https://courses.cs.washington.edu/courses/cse410/26au/
sources:
  - type: demo
  - type: cse_calendar
    name: cse410
    course: CSE 410
    base_url: {(FIXTURES / "cse410").as_posix()}
    calendars: [lecture-A, oh]
    lookback_days: 3650
    lookahead_days: 3650
  - type: canvas_feed
    url: ${{DASHBOARD_TEST_UNSET_VAR}}
  - type: canvas
    cookie: ""
"""


def make_client(tmp_path) -> TestClient:
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    return TestClient(create_app(settings))


def test_sync_then_query(tmp_path):
    with make_client(tmp_path) as client:
        statuses = {s["name"]: s for s in client.post("/api/sync").json()}
        assert statuses["demo"]["last_run"]["ok"] and statuses["cse410"]["last_run"]["ok"]
        assert "DASHBOARD_TEST_UNSET_VAR" in statuses["canvas_feed"]["config_error"]
        assert "exactly one of token or cookie" in statuses["canvas"]["config_error"]

        items = client.get("/api/items", params={"kind": ["assignment", "exam"]}).json()
        assert items and {i["kind"] for i in items} == {"assignment", "exam"}
        ats = [i["at"] for i in items]
        assert ats == sorted(ats)

        cse = client.get("/api/items", params={"course": "cse410", "kind": "assignment"}).json()
        assert {i["title"] for i in cse} >= {"PSet 2", "Lab 2 Part 1 DD"}
        assert cse[0]["course"]["color"] == "#4b2e83"

        missing = client.get("/api/items", params={"status": "missing"}).json()
        assert [i["title"] for i in missing] == ["Quiz 2"]

        courses = {c["id"]: c for c in client.get("/api/courses").json()}
        assert {"cse410", "demo101", "demo202", "demo303"} <= set(courses)
        assert courses["cse410"]["links"]["site"].endswith("/cse410/26au/")


def test_items_from_sources_no_longer_configured_are_hidden(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/api/sources/demo/sync")
        old = ItemIn(source_id="x", kind="assignment", title="From a removed source", due_at=datetime.now(timezone.utc))
        store_result(client.app.state.Session, "removed-source", FetchResult(items=[old]), datetime.now(timezone.utc))

        titles = {i["title"] for i in client.get("/api/items").json()}
        assert "From a removed source" not in titles
        assert "Problem set 3" in titles  # demo still shows

        # Courses only that removed source knew about don't count as active.
        gone = ItemIn(source_id="y", kind="assignment", title="Old", due_at=datetime.now(timezone.utc),
                      course=CourseRef(code="CHEM 142"))  # fmt: skip
        store_result(client.app.state.Session, "removed-source", FetchResult(items=[gone]), datetime.now(timezone.utc))
        active = {c["id"] for c in client.get("/api/courses", params={"active": True}).json()}
        assert active == {"demo101", "demo202", "demo303"}
        assert "chem142" in {c["id"] for c in client.get("/api/courses").json()}


def test_window_filter(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/api/sources/cse410/sync")
        items = client.get("/api/items", params={
            "start": "2026-10-28T00:00:00-07:00", "end": "2026-10-31T00:00:00-07:00", "source": "cse410",
        }).json()  # fmt: skip
        assert [i["title"] for i in items] == ["PSet 2", "Lab 2 Part 1 DD"]
        assert client.post("/api/sources/nope/sync").status_code == 404


def test_page_addresses_load_the_app(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("sources: []\n", encoding="utf-8")
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", static_dir=web, sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        for page in ("/", "/tutor", "/tutor/12"):
            r = c.get(page)
            assert (r.status_code, r.text) == (200, "<div id=root></div>"), page
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert "access-control-allow-origin" not in c.get("/assets/app.js").headers
        assert c.get("/assets/missing.js").status_code == 404
        assert c.get("/api/nothing-here").status_code == 404
        assert c.get("/api/agent").status_code == 200


def test_diagram_libraries_load_in_the_sealed_frame(tmp_path):
    """The agent's diagrams run in a frame with no origin, so /viz/ (and only /viz/) answers cross-origin."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    web = tmp_path / "web"
    (web / "viz" / "three").mkdir(parents=True)
    (web / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (web / "viz" / "three" / "three.module.js").write_text("export const REVISION = '186';", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("sources: []\n", encoding="utf-8")
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", static_dir=web, sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        r = c.get("/viz/three/three.module.js", headers={"Origin": "null"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "*"
        assert "javascript" in r.headers["content-type"]  # module scripts are refused without a JS type
        assert c.get("/viz/missing.js").status_code == 404
        assert "access-control-allow-origin" not in c.get("/").headers
