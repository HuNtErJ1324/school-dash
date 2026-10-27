from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CONFIG = """
quarter: 26au
sources:
  - type: demo
"""


def make_client(tmp_path) -> TestClient:
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    return TestClient(create_app(settings))


def by_title(client: TestClient, **params) -> dict[str, dict]:
    return {i["title"]: i for i in client.get("/api/items", params=params).json()}


def test_overlay_round_trip_and_survives_resync(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/api/sync")
        ps3 = by_title(client)["Problem set 3"]
        assert (ps3["done"], ps3["hidden"], ps3["pinned"], ps3["note"]) == (False, False, False, None)

        r = client.patch(f"/api/items/{ps3['id']}/overlay", json={"done": True, "note": "  Q4 left  "})
        assert r.status_code == 200
        assert (r.json()["done"], r.json()["note"], r.json()["title"]) == (True, "Q4 left", "Problem set 3")

        client.post("/api/sync")  # a sync rewrites the item but never the overlay
        again = by_title(client)["Problem set 3"]
        assert (again["done"], again["note"], again["id"]) == (True, "Q4 left", ps3["id"])

        # Omitted and null fields are left alone; an empty note clears it.
        client.patch(f"/api/items/{ps3['id']}/overlay", json={"pinned": True, "done": None})
        cleared = client.patch(f"/api/items/{ps3['id']}/overlay", json={"note": ""}).json()
        assert (cleared["done"], cleared["pinned"], cleared["note"]) == (True, True, None)


def test_hidden_and_pinned_filters(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/api/sync")
        items = by_title(client)
        client.patch(f"/api/items/{items['Quiz 2']['id']}/overlay", json={"hidden": True})
        client.patch(f"/api/items/{items['Lab 3']['id']}/overlay", json={"pinned": True})

        assert "Quiz 2" not in by_title(client)
        assert by_title(client, include_hidden=True)["Quiz 2"]["hidden"] is True
        assert list(by_title(client, pinned=True)) == ["Lab 3"]
        assert "Lab 3" not in by_title(client, pinned=False)


def test_overlay_on_missing_item_is_404(tmp_path):
    with make_client(tmp_path) as client:
        assert client.patch("/api/items/9999/overlay", json={"done": True}).status_code == 404


def test_boards_save_and_load(tmp_path):
    board = {
        "widgets": [{"id": "up-next", "type": "up-next", "options": {"days": 10}}],
        "layouts": {"lg": [{"i": "up-next", "x": 0, "y": 0, "w": 8, "h": 12}]},
    }
    with make_client(tmp_path) as client:
        assert client.get("/api/boards/default").status_code == 404
        saved = client.put("/api/boards/default", json=board).json()
        assert saved["widgets"] == board["widgets"] and saved["name"] == "default"

        loaded = client.get("/api/boards/default").json()
        assert (loaded["widgets"], loaded["layouts"]) == (board["widgets"], board["layouts"])
        assert client.put("/api/boards/Bad Name!", json=board).status_code == 422
