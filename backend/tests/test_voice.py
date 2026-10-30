from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, VoiceConfig
from app.main import create_app
from app.voice import SpeechClient

CONFIG = """
courses:
  - code: CSE 410
    name: Operating Systems
  - code: MATH 334
sources: []
"""

WHISPER = "deepdml/faster-whisper-large-v3-turbo-ct2"
KOKORO = "speaches-ai/Kokoro-82M-v1.0-ONNX"


class FakeSpeaches:
    """Answers like the speech container: its installed models, transcripts and MP3s."""

    def __init__(self) -> None:
        self.installed = [WHISPER, KOKORO]
        self.requests: list[httpx.Request] = []
        self.fail: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(self.fail, json={"detail": "nope"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m} for m in self.installed]})
        if request.url.path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": " What's a semaphore? "})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=b"ID3 mp3", headers={"Content-Type": "audio/mpeg"})
        return httpx.Response(404)


@pytest.fixture
def client(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        c.fake = FakeSpeaches()
        http = httpx.AsyncClient(transport=httpx.MockTransport(c.fake))
        c.app.state.speech = SpeechClient("http://speech:8000/", VoiceConfig(voice="bf_emma"), http)
        yield c


def form(request: httpx.Request) -> dict[str, Any]:
    """The plain fields of a multipart request, and the file's name and type."""
    body = request.read().decode()
    fields: dict[str, Any] = {}
    for part in body.split("--" + request.headers["content-type"].split("boundary=")[1])[1:-1]:
        head, _, value = part.strip("\r\n").partition("\r\n\r\n")
        name = head.split('name="')[1].split('"')[0]
        if 'filename="' in head:
            fields[name] = (head.split('filename="')[1].split('"')[0], head.split("Content-Type: ")[1].strip())
        else:
            fields[name] = value
    return fields


def test_status_says_what_the_speech_container_can_do(client):
    assert client.get("/api/voice").json() == {"stt": True, "tts": True, "problem": None}
    client.fake.installed = [WHISPER]
    assert client.get("/api/voice").json() == {
        "stt": True, "tts": False, "problem": f"The speech container hasn't downloaded {KOKORO}.",
    }  # fmt: skip
    client.fake.fail = 500
    status = client.get("/api/voice").json()
    assert not status["stt"] and not status["tts"] and "isn't answering" in status["problem"]


def test_without_a_speech_container(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG)
    settings = Settings(config_path=tmp_path / "config.yaml", data_dir=tmp_path / "data", sync_on_start=False)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/voice").json() == {
            "stt": False, "tts": False, "problem": "There's no speech container: SPEECH_URL isn't set.",
        }  # fmt: skip
        r = c.post("/api/voice/transcribe", files={"file": ("speech.webm", b"audio", "audio/webm")})
        assert r.status_code == 503
        assert c.post("/api/voice/speak", json={"text": "Hi."}).status_code == 503


def test_transcribing_tells_whisper_about_the_courses_and_the_topic(client):
    r = client.post(
        "/api/voice/transcribe",
        files={"file": ("speech.webm", b"opus audio", "audio/webm;codecs=opus")},
        data={"context": "CSE 410 · Locks and semaphores"},
    )
    assert r.status_code == 200
    assert r.json() == {"text": "What's a semaphore?"}
    sent = form(client.fake.requests[-1])
    assert sent == {
        "model": WHISPER,
        "response_format": "json",
        "temperature": "0",
        "language": "en",
        "prompt": "CSE 410 Operating Systems, MATH 334. CSE 410 · Locks and semaphores",
        "file": ("speech.webm", "audio/webm;codecs=opus"),
    }


def test_transcribing_problems(client):
    empty = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"", "audio/webm")})
    assert empty.status_code == 422
    big = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"x" * (10 * 1024 * 1024 + 1), "audio/webm")})
    assert big.status_code == 413
    assert not client.fake.requests
    client.fake.fail = 415
    r = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"noise", "audio/webm")})
    assert r.status_code == 422 and r.json()["detail"] == "That recording couldn't be read. Try again?"
    client.fake.fail = 404
    r = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"noise", "audio/webm")})
    assert r.status_code == 503 and "config.yaml" in r.json()["detail"]


def test_speaking_reads_in_the_configured_voice(client):
    r = client.post("/api/voice/speak", json={"text": "A semaphore counts."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3 mp3"
    assert json.loads(client.fake.requests[-1].content) == {
        "model": KOKORO, "voice": "bf_emma", "input": "A semaphore counts.", "response_format": "mp3", "speed": 1.0,
    }  # fmt: skip
    assert client.post("/api/voice/speak", json={"text": ""}).status_code == 422
    assert client.post("/api/voice/speak", json={"text": "x" * 2001}).status_code == 422
    client.fake.fail = 400
    r = client.post("/api/voice/speak", json={"text": "Hi."})
    assert r.status_code == 422 and "bf_emma" in r.json()["detail"]
