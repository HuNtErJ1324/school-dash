from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, VoiceConfig
from app.main import create_app
from app.voice import HEX_DOWN, HexClient, SpeechClient

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


PARAKEET = "parakeet_unified_en"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"  # what the page sends once it has converted a recording


class FakeHex:
    """Answers like Hex through its relay (hex_relay.py)."""

    def __init__(self) -> None:
        self.installed = [PARAKEET]
        self.requests: list[httpx.Request] = []
        self.fail: tuple[int, str] | None = None
        self.relay_down = False  # its socket is left, but nothing listens

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.relay_down:
            raise httpx.ConnectError("Connection refused")
        if self.fail:
            return httpx.Response(self.fail[0], json={"code": self.fail[1]})
        if request.url.path == "/models":
            models = [{"id": m, "installed": True} for m in self.installed] + [{"id": "parakeet_v3", "installed": False}]
            return httpx.Response(200, json=models)
        if request.url.path == "/transcriptions":
            return httpx.Response(200, json={"durationMs": 1500, "transcript": " Ask what you can do. "})
        return httpx.Response(404, json={"code": "not-found"})


@pytest.fixture
def fake_hex(client, tmp_path):
    """Hex set up next to the speech container: the relay's socket is there."""
    socket = tmp_path / "relay.sock"
    socket.touch()
    fake = FakeHex()
    client.app.state.speech.hex = HexClient(socket, VoiceConfig(), httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    return fake


def test_hex_writes_down_what_you_say(client, fake_hex):
    r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")}, data={"context": "x"})
    assert r.status_code == 200 and r.json() == {"text": "Ask what you can do."}
    sent = fake_hex.requests[-1]
    assert sent.method == "POST" and sent.url.path == "/transcriptions"
    assert dict(sent.url.params) == {"model": PARAKEET, "language": "en"}
    assert sent.headers["content-type"] == "audio/wav" and sent.content == WAV
    assert not client.fake.requests  # Whisper wasn't asked


def test_whisper_stands_in_only_while_hex_isnt_running(client, fake_hex):
    fake_hex.fail = (503, "hex-not-running")
    r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")}, data={"context": "x"})
    assert r.status_code == 200 and r.json() == {"text": "What's a semaphore?"}
    assert form(client.fake.requests[-1])["prompt"] == "CSE 410 Operating Systems, MATH 334. x"
    # A recording the page couldn't convert to WAV goes to Whisper as it is, while Hex isn't running.
    r = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"opus audio", "audio/webm")})
    assert r.json() == {"text": "What's a semaphore?"}
    assert len(client.fake.requests) == 2


def test_while_hex_runs_whisper_stays_idle(client, fake_hex):
    for fail, status, detail in [
        ((503, "busy"), 503, "Hex couldn't do it (busy)."),
        ((429, "queue-full"), 503, "Hex couldn't do it (queue-full)."),
        ((503, "model-not-ready"), 503, "Hex couldn't do it (model-not-ready)."),
        ((415, "unsupported-audio"), 422, "That recording couldn't be read. Try again?"),
    ]:
        fake_hex.fail = fail
        r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")})
        assert (r.status_code, r.json()["detail"]) == (status, detail), fail
    fake_hex.fail = None
    r = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"opus audio", "audio/webm")})
    assert r.status_code == 422 and r.json()["detail"] == "Hex needs WAV, and this browser's recording isn't."
    assert not client.fake.requests


def test_when_neither_can(client, fake_hex):
    fake_hex.fail = (503, "hex-not-running")
    client.fake.fail = 500
    r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")})
    assert r.status_code == 502
    assert r.json()["detail"] == f"{HEX_DOWN} The speech container returned HTTP 500."


def test_hex_without_whisper(client, fake_hex):
    client.app.state.speech.config = VoiceConfig(stt_model="")
    fake_hex.fail = (503, "hex-not-running")
    r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")})
    assert r.status_code == 503 and r.json()["detail"] == HEX_DOWN
    r = client.post("/api/voice/transcribe", files={"file": ("speech.webm", b"opus audio", "audio/webm")})
    assert r.status_code == 503 and r.json()["detail"] == HEX_DOWN
    assert client.get("/api/voice").json() == {"stt": False, "tts": True, "problem": HEX_DOWN}
    fake_hex.fail = None
    assert client.get("/api/voice").json() == {"stt": True, "tts": True, "problem": None}
    assert {q.url.path for q in client.fake.requests} == {"/v1/models"}  # only the status checks


def test_status_with_hex(client, fake_hex):
    assert client.get("/api/voice").json() == {"stt": True, "tts": True, "problem": None}
    fake_hex.fail = (503, "hex-not-running")
    assert client.get("/api/voice").json() == {
        "stt": True, "tts": True, "problem": f"{HEX_DOWN} Whisper writes down what you say until it's back.",
    }  # fmt: skip
    client.fake.fail = 500
    status = client.get("/api/voice").json()
    assert not status["stt"] and not status["tts"]
    assert status["problem"].startswith(f"{HEX_DOWN} The speech container isn't answering")
    fake_hex.fail, client.fake.fail = None, None
    # Hex runs but can't take dictation: the mic hides, and Whisper doesn't stand in.
    fake_hex.installed = []
    assert client.get("/api/voice").json() == {
        "stt": False, "tts": True, "problem": f"Hex hasn't installed {PARAKEET} (`hex model list` shows what it has).",
    }  # fmt: skip
    fake_hex.fail = (503, "hex-file-unreadable")
    assert client.get("/api/voice").json() == {
        "stt": False, "tts": True, "problem": "Hex isn't answering (hex-file-unreadable).",
    }  # fmt: skip


def test_a_dead_relay(client, fake_hex):
    # Its socket is left but nothing answers: Hex may still be running, so Whisper doesn't stand in.
    fake_hex.relay_down = True
    r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")})
    assert r.status_code == 503 and r.json()["detail"] == "Hex's relay isn't running (`docker compose ps hex`)."
    assert client.get("/api/voice").json() == {
        "stt": False, "tts": True, "problem": "Hex's relay isn't running (`docker compose ps hex`).",
    }  # fmt: skip
    assert len(client.fake.requests) == 1  # the status check's model list


def test_hex_isnt_used_without_its_relay(client, fake_hex):
    client.app.state.speech.hex.socket.unlink()  # docker-compose.yml's `hex` service isn't on
    r = client.post("/api/voice/transcribe", files={"file": ("speech.wav", WAV, "audio/wav")})
    assert r.json() == {"text": "What's a semaphore?"}
    assert client.get("/api/voice").json() == {"stt": True, "tts": True, "problem": None}
    assert not fake_hex.requests


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
