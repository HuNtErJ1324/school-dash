from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.cookies import NOT_A_COOKIE, CookieStore, parse_cookie
from app.main import create_app
from conftest import FIXTURES

COOKIE = "log_session_id=abc; canvas_session=x%2By"


@pytest.mark.parametrize(
    "pasted",
    [
        COOKIE,
        f"'{COOKIE}'",
        f"Cookie: {COOKIE}",
        "cookie:log_session_id=abc;canvas_session=x%2By;",
        # Chrome's request headers, HTTP/2 names and all
        f":authority: canvas.uw.edu\n:method: GET\naccept: text/html\ncookie: {COOKIE}\nuser-agent: Mozilla/5.0\n",
        # Copy as cURL (bash), from Chrome
        f"curl 'https://canvas.uw.edu/' \\\n  -H 'accept: text/html' \\\n  -b '{COOKIE}' \\\n  -H 'user-agent: Mozilla/5.0'",
        # Copy as cURL, from Firefox
        f"curl 'https://canvas.uw.edu/' --compressed -H 'User-Agent: Mozilla/5.0' -H 'Cookie: {COOKIE}' -H 'TE: trailers'",
        # Copy as cURL (cmd), from Chrome on Windows: ^ escapes, so % comes out as ^%
        'curl ^"https://canvas.uw.edu/^" ^\r\n  -H ^"accept: text/html^" ^\r\n'
        '  -b ^"log_session_id=abc; canvas_session=x^%^2By^" ^\r\n  -H ^"user-agent: Mozilla/5.0^"',
        # Copy as fetch (Node.js)
        f'fetch("https://canvas.uw.edu/", {{\n  "headers": {{\n    "accept": "text/html",\n    "cookie": "{COOKIE}"\n  }},\n'
        '  "method": "GET"\n});',
    ],
)
def test_cookie_from_whatever_you_copied(pasted):
    assert parse_cookie(pasted) == COOKIE


@pytest.mark.parametrize(
    "pasted",
    ["", "   ", "hello world", "curl -b cookies.txt https://canvas.uw.edu/", "a=1\x07", "secret=1\nother=2",
     pytest.param("x=" * 150_000, id="too-long")],
)
def test_what_isnt_a_cookie_is_refused_without_quoting_it(pasted):
    with pytest.raises(ValueError) as e:
        parse_cookie(pasted)
    assert pasted.strip() == "" or pasted not in str(e.value)


def test_curl_without_a_cookie_says_so():
    with pytest.raises(ValueError, match="has no cookie"):
        parse_cookie("curl 'https://canvas.uw.edu/' -H 'accept: text/html'")


def test_store_keeps_pasted_cookies_until_env_has_a_new_one(tmp_path):
    path = tmp_path / "data" / "cookies.json"
    store = CookieStore(path)
    store.save("canvas", "canvas_session=pasted", "canvas_session=from-env", datetime.now(timezone.utc))
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600

    store = CookieStore(path)  # a restart
    assert store.get("canvas").saved_at.tzinfo is not None
    assert store.pasted("canvas", "canvas_session=from-env") == "canvas_session=pasted"  # .env unchanged
    assert store.pasted("canvas", None) == "canvas_session=pasted"  # .env's emptied: still the pasted one
    assert store.pasted("gradescope", "signed_token=1") is None

    # A new cookie in .env is newer than the pasted one, so it wins, and the pasted one is gone for good.
    assert store.pasted("canvas", "canvas_session=newer-env") is None
    assert CookieStore(path).get("canvas") is None


def test_unreadable_store_is_ignored_without_logging_its_contents(tmp_path, caplog):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps({"canvas": {"cookie": "canvas_session=s3cret"}}))  # no saved_at
    with caplog.at_level(logging.WARNING):
        assert CookieStore(path).get("canvas") is None
    assert "s3cret" not in caplog.text


# ---- The API, against made-up Canvas and Gradescope servers ----------------------------------------------------

CANVAS = "https://canvas.example.edu"
GRADESCOPE = "https://gs.example.com"
CONFIG = f"""
sources:
  - type: canvas
    base_url: {CANVAS}
    cookie: ${{DASHBOARD_TEST_CANVAS_COOKIE}}
  - type: gradescope
    base_url: {GRADESCOPE}
    cookie: ${{DASHBOARD_TEST_UNSET_GRADESCOPE_COOKIE}}
    pause_seconds: 0
"""
FRESH = "canvas_session=fresh-s3cret; _csrf_token=abc%2Bdef"


class FakeSites:
    """Canvas lets in the cookies in `good`; Gradescope lets in signed_token=good."""

    def __init__(self) -> None:
        self.good = {"canvas_session=fresh-s3cret"}
        self.seen: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        cookie = request.headers.get("cookie", "")
        if request.url.host == "gs.example.com":
            if "signed_token=good" not in cookie:
                return httpx.Response(302, headers={"Location": f"{GRADESCOPE}/login"})
            if request.url.path == "/account":
                return httpx.Response(200, text=(FIXTURES / "gradescope-account.html").read_text())
            return httpx.Response(200, text=(FIXTURES / "gradescope-course.html").read_text())
        if not any(g in cookie for g in self.good):
            return httpx.Response(401, json={"status": "unauthenticated"})

        def page(data: object) -> httpx.Response:
            return httpx.Response(200, text="while(1);" + json.dumps(data), headers={"Content-Type": "application/json"})

        path = request.url.path
        if path == "/api/v1/users/self":
            return page({"id": 1})
        if path == "/api/v1/courses":
            return page([{"id": 77, "course_code": "MATH 300 A", "name": "Analysis", "term": {"name": "Autumn 2026"}}])
        if path in ("/api/v1/planner/items", "/api/v1/courses/77/assignment_groups"):
            return page([])
        if path == "/api/v1/courses/77/assignments":
            return page([{"id": 5, "name": "HW4", "due_at": "2026-10-30T06:59:00Z", "points_possible": 10,
                          "submission_types": ["online_upload"], "description": "<p>Do problems 1-4.</p>",
                          "html_url": f"{CANVAS}/courses/77/assignments/5"}])  # fmt: skip
        if path == "/api/v1/courses/77":
            return page({"id": 77, "syllabus_body": "<p>Homework is 30%.</p>"})
        return httpx.Response(404)


@pytest.fixture
def sites() -> FakeSites:
    return FakeSites()


def start(tmp_path, sites: FakeSites) -> TestClient:
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    client = TestClient(create_app(settings))
    client.__enter__()
    http = httpx.AsyncClient(transport=sites.transport)
    client.app.state.source_ctx.http = http  # what sources sync and check logins with
    client.app.state.library_sync.http = http
    return client


def status(client: TestClient, name: str) -> dict:
    return next(s for s in client.get("/api/sources").json() if s["name"] == name)


def wait_for_library(client: TestClient) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        library = client.get("/api/library").json()
        if not library["running"] and any(r["name"] == "canvas" for r in library["runs"]):
            return next(r for r in library["runs"] if r["name"] == "canvas")
        time.sleep(0.05)
    raise AssertionError("the library never read Canvas")


def test_expired_canvas_cookie_is_replaced_by_a_pasted_one(tmp_path, sites, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TEST_CANVAS_COOKIE", "canvas_session=expired")
    client = start(tmp_path, sites)
    responses: list[str] = []
    try:
        # The cookie from .env has expired: the run says a login would fix it.
        run = client.post("/api/sources/canvas/sync").json()
        assert run["last_run"]["needs_login"] and "fresh session cookie" in run["last_run"]["error"]
        cookie = status(client, "canvas")["cookie"]
        assert (cookie["origin"], cookie["site"], cookie["check_ok"]) == ("config", "canvas.example.edu", False)

        # A web page can't paste one, and nothing is asked of Canvas.
        sites.seen.clear()
        r = client.put("/api/sources/canvas/cookie", json={"cookie": FRESH}, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403 and not sites.seen

        # Not a cookie: refused without asking Canvas, and without quoting it back.
        r = client.put("/api/sources/canvas/cookie", json={"cookie": "my canvas password"})
        assert (r.status_code, r.json()["detail"]) == (422, NOT_A_COOKIE) and not sites.seen
        assert "password" not in r.text

        # A cookie Canvas turns away: one GET to check it, and it isn't kept.
        r = client.put("/api/sources/canvas/cookie", json={"cookie": "Cookie: canvas_session=also-expired"})
        assert r.status_code == 422 and "canvas.example.edu didn't take that cookie" in r.json()["detail"]
        assert [(q.method, q.url.path) for q in sites.seen] == [("GET", "/api/v1/users/self")]
        assert not (tmp_path / "data" / "cookies.json").exists()

        # A good one, as Copy as cURL: checked with one GET, kept, used at once, and Canvas and its documents re-read.
        sites.seen.clear()
        curl = f"curl '{CANVAS}/' \\\n  -H 'accept: text/html' \\\n  -b '{FRESH}' \\\n  -H 'user-agent: Mozilla/5.0'"
        r = client.put("/api/sources/canvas/cookie", json={"cookie": curl})
        assert r.status_code == 200, r.text
        responses.append(r.text)
        saved = r.json()
        assert saved["last_run"]["ok"] and saved["last_run"]["items_seen"] == 1
        assert (saved["cookie"]["origin"], saved["cookie"]["check_ok"]) == ("pasted", True)
        assert saved["cookie"]["saved_at"] and saved["cookie"]["checked_at"]
        assert sites.seen[0].url.path == "/api/v1/users/self"
        assert wait_for_library(client)["error"] is None
        assert {q.method for q in sites.seen} == {"GET"}  # read-only
        assert all(q.headers["cookie"] == FRESH for q in sites.seen)
        library = client.get("/api/library").json()
        assert any(d["title"].startswith("Syllabus") or d["kind"] == "syllabus" for d in library["documents"])

        responses += [client.get("/api/sources").text, client.get("/api/library").text]
        assert not any("fresh-s3cret" in text for text in responses)  # the page never gets it back
        stored = json.loads((tmp_path / "data" / "cookies.json").read_text())
        assert stored["canvas"]["cookie"] == FRESH
    finally:
        client.__exit__(None, None, None)

    # After a restart the pasted cookie still beats the expired one in .env.
    client = start(tmp_path, sites)
    try:
        assert client.post("/api/sources/canvas/sync").json()["last_run"]["ok"]
        assert status(client, "canvas")["cookie"]["origin"] == "pasted"

        # Then it expires too, and the dashboard says so.
        sites.good.clear()
        run = client.post("/api/sources/canvas/sync").json()
        assert run["last_run"]["needs_login"] and run["cookie"]["check_ok"] is False
    finally:
        client.__exit__(None, None, None)

    # A new cookie put in .env the old way is newer than the pasted one, so .env works as before.
    monkeypatch.setenv("DASHBOARD_TEST_CANVAS_COOKIE", "canvas_session=newer")
    sites.good = {"canvas_session=newer"}
    client = start(tmp_path, sites)
    try:
        run = client.post("/api/sources/canvas/sync").json()
        assert run["last_run"]["ok"] and run["cookie"]["origin"] == "config"
    finally:
        client.__exit__(None, None, None)


def test_pasting_a_cookie_starts_a_source_that_had_none(tmp_path, sites):
    client = start(tmp_path, sites)
    try:
        before = status(client, "gradescope")
        assert "DASHBOARD_TEST_UNSET_GRADESCOPE_COOKIE" in before["config_error"]
        assert before["cookie"] == {"origin": None, "site": "gs.example.com", "saved_at": None, "checked_at": None,
                                    "check_ok": None}  # fmt: skip

        headers = "GET /account HTTP/1.1\nHost: gs.example.com\nCookie: signed_token=good; _gradescope_session=x\n"
        r = client.put("/api/sources/gradescope/cookie", json={"cookie": headers})
        assert r.status_code == 200, r.text
        after = r.json()
        assert after["config_error"] is None and after["last_run"]["ok"] and after["last_run"]["items_seen"] > 0
        assert after["cookie"]["origin"] == "pasted"
        assert "gradescope" in client.app.state.scheduler.sources
        assert "gradescope" not in [p.name for p in client.app.state.problems]
        titles = {i["title"] for i in client.get("/api/items", params={"source": "gradescope"}).json()}
        assert "PSet 1" in titles

        assert client.put("/api/sources/nope/cookie", json={"cookie": "a=1"}).status_code == 404
    finally:
        client.__exit__(None, None, None)
