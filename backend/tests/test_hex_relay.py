from __future__ import annotations

import http.client
import json
import os
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app import hex_relay
from app.hex_relay import Relay


class FakeHex(BaseHTTPRequestHandler):
    """Answers like Hex's local API, for the token in `server.token`."""

    def do_GET(self) -> None:
        self.answer(None)

    def do_POST(self) -> None:
        self.answer(self.rfile.read(int(self.headers["Content-Length"])))

    def answer(self, body: bytes | None) -> None:
        server: Any = self.server
        server.seen.append((self.command, self.path, dict(self.headers), body))
        if self.headers.get("Authorization") != f"Bearer {server.token}":
            server.on_bad_token()
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        reply = {"transcript": "What's a semaphore?", "durationMs": 1500} if self.command == "POST" else {"ok": True}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


def serve(server: socketserver.BaseServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def hex_api(tmp_path) -> Iterator[Any]:
    server: Any = ThreadingHTTPServer(("127.0.0.1", 0), FakeHex)
    server.token, server.seen, server.on_bad_token = "hex_token", [], lambda: None
    server.publish = lambda port=server.server_address[1], token=server.token: (tmp_path / "local-api.json").write_text(
        json.dumps({"port": port, "token": token, "apiVersion": "2", "pid": 1})
    )
    server.publish()
    serve(server)
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def relay(tmp_path) -> Iterator[int]:
    """The relay's port, over TCP here (Windows has no Unix sockets); test_unix_socket covers the socket."""
    server: Any = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Relay)
    server.daemon_threads, server.hex_dir = True, tmp_path
    serve(server)
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def ask(port: int, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        if body is None and headers is None:
            conn.request(method, path)
        else:
            conn.putrequest(method, path)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


def test_passes_a_recording_to_hex_with_its_token(hex_api, relay):
    status, body = ask(
        relay, "POST", "/transcriptions?model=parakeet_unified_en&language=en&prompt=Riemann",
        WAV, {"Content-Type": "audio/wav", "Content-Length": str(len(WAV))},
    )  # fmt: skip
    assert status == 200 and json.loads(body)["transcript"] == "What's a semaphore?"
    method, path, headers, sent = hex_api.seen[-1]
    assert (method, path, sent) == ("POST", "/transcriptions?model=parakeet_unified_en&language=en", WAV)
    assert headers["Authorization"] == "Bearer hex_token" and headers["Content-Type"] == "audio/wav"


def test_only_what_dictation_needs(hex_api, relay):
    assert ask(relay, "GET", "/health")[0] == 200
    assert ask(relay, "GET", "/models?language=en")[0] == 200
    for method, path in [("POST", "/control"), ("POST", "/streams"), ("GET", "/history"), ("GET", "/transcriptions"),
                         ("POST", "/models/parakeet_v3/prepare"), ("GET", "/health/../control")]:  # fmt: skip
        status, body = ask(relay, method, path, b"{}", {"Content-Length": "2"})
        assert status == 404 and json.loads(body) == {"code": "not-found"}, path
    assert [p for _, p, _, _ in hex_api.seen] == ["/health", "/models?language=en"]


def test_when_hex_isnt_running(tmp_path, relay):
    status, body = ask(relay, "GET", "/health")
    assert status == 503 and json.loads(body) == {"code": "hex-not-running"}  # no local-api.json
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    (tmp_path / "local-api.json").write_text(json.dumps({"port": closed, "token": "t"}))
    assert ask(relay, "GET", "/health")[0] == 503  # its file is left, but nothing listens


def test_when_hex_s_file_cant_be_read(tmp_path, relay, capfd):
    (tmp_path / "local-api.json").write_text('{"port": 1')  # Hex was writing it, or it isn't Hex's
    status, body = ask(relay, "GET", "/health")
    assert status == 503 and json.loads(body) == {"code": "hex-file-unreadable"}
    assert "JSONDecodeError" in capfd.readouterr().err


def test_follows_hex_to_a_new_port_and_token(hex_api, relay, tmp_path):
    assert ask(relay, "GET", "/health")[0] == 200
    new: Any = ThreadingHTTPServer(("127.0.0.1", 0), FakeHex)
    new.token, new.seen, new.on_bad_token = "new_token", [], lambda: None
    serve(new)
    try:
        hex_api.publish(new.server_address[1], "new_token")  # Hex restarted
        assert ask(relay, "GET", "/health")[0] == 200
        assert new.seen[-1][2]["Authorization"] == "Bearer new_token"
    finally:
        new.shutdown()
        new.server_close()


def test_reads_hex_s_file_again_when_the_token_is_refused(hex_api, relay):
    # Hex restarts after the relay read its file: the old token is refused once, then the new one works.
    hex_api.token = "after_restart"
    hex_api.on_bad_token = lambda: hex_api.publish(token="after_restart")
    assert ask(relay, "GET", "/health")[0] == 200
    hex_api.on_bad_token = lambda: None
    hex_api.token = "never"
    status, body = ask(relay, "GET", "/health")
    assert status == 502 and json.loads(body) == {"code": "hex-refused-token"}


def test_uploads_need_a_length_and_a_size_limit(hex_api, relay, monkeypatch):
    status, body = ask(relay, "POST", "/transcriptions?model=m", None, {"Content-Type": "audio/wav"})
    assert status == 411 and json.loads(body) == {"code": "length-required"}
    monkeypatch.setattr(hex_relay, "MAX_BODY", 10)
    status, body = ask(relay, "POST", "/transcriptions?model=m", b"x" * 11, {"Content-Length": "11"})
    assert status == 413 and json.loads(body) == {"code": "audio-too-large"}
    assert not hex_api.seen


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="Unix sockets")
def test_unix_socket(hex_api, tmp_path):
    path = tmp_path / "relay.sock"
    path.write_text("left by the last run")
    server = hex_relay.unix_server(path, tmp_path)
    serve(server)
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(path))
            s.sendall(b"GET /health HTTP/1.1\r\nHost: hex\r\nConnection: close\r\n\r\n")
            reply = b""
            while chunk := s.recv(65536):
                reply += chunk
        assert reply.startswith(b"HTTP/1.0 200") and reply.endswith(b'{"ok": true}')
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(os.name == "nt", reason="Unix sockets and SIGTERM")
def test_stopping_removes_the_socket(tmp_path):
    path = tmp_path / "relay.sock"
    env = {**os.environ, "HEX_DIR": str(tmp_path), "HEX_SOCKET": str(path)}
    relay = subprocess.Popen([sys.executable, "-m", "app.hex_relay"], env=env, cwd=Path(__file__).parents[1])
    try:
        for _ in range(100):
            if path.exists():
                break
            time.sleep(0.05)
        assert path.is_socket()
        relay.terminate()  # what `docker compose stop` sends
        assert relay.wait(10) == 0
        assert not path.exists()
    finally:
        relay.kill()
