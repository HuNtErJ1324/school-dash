"""The app's way to Hex (github.com/anomalyco/hex), the dictation app on the server's desktop.

Hex's own API listens only on the server's 127.0.0.1, on a port and with a token it picks each time it starts (its
local-api.json, readable only by you), and the app's container can't reach that address. So docker-compose.yml's
`hex` service runs this on the host's network: it listens on a Unix socket in ./data/hex that only your user can open
(the app runs as you), reads local-api.json for every request, and passes on only what dictation needs: GET /health,
GET /models and POST /transcriptions. Hex's settings, history and the desktop's microphone aren't reachable through it.

Run with: python -m app.hex_relay"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socketserver
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

ROUTES = {("GET", "/health"), ("GET", "/models"), ("POST", "/transcriptions")}
QUERY = {"model", "language"}  # all /transcriptions takes
MAX_BODY = 16 * 1024 * 1024  # the app sends at most 10 MB; Hex takes up to 64 MiB
TIMEOUT = 120  # seconds: Hex may reload a cold model first


class HexNotRunning(Exception):
    pass


class HexFileUnreadable(Exception):
    """local-api.json is there but can't be read: a wrong HEX_DIR or PUID, or Hex was writing it."""


def discover(hex_dir: Path) -> tuple[int, str]:
    """Hex's port and token now, from the file it writes each time it starts."""
    path = hex_dir / "local-api.json"
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        return int(found["port"]), str(found["token"])
    except FileNotFoundError as e:
        raise HexNotRunning from e
    except (OSError, ValueError, KeyError, TypeError) as e:
        sys.stderr.write(f"can't read {path}: {type(e).__name__}\n")  # never what's in it
        raise HexFileUnreadable from e


def forward(hex_dir: Path, method: str, path: str, body: bytes | None, content_type: str) -> tuple[int, str, bytes]:
    """Hex's (status, content type, body) for one request. Tries twice: Hex may have restarted, onto a new port and
    token, between reading its file and asking it."""
    for attempt in (1, 2):
        port, token = discover(hex_dir)
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = content_type
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            status, data = resp.status, resp.read()
            kind = resp.getheader("Content-Type") or "application/json"
        except ConnectionRefusedError as e:
            if attempt == 2:
                raise HexNotRunning from e
            continue
        except TimeoutError:
            return 504, "application/json", b'{"code":"hex-timeout"}'
        except (OSError, http.client.HTTPException):
            return 502, "application/json", b'{"code":"hex-failed"}'
        finally:
            conn.close()
        if status != 401:
            return status, kind, data
    return 502, "application/json", b'{"code":"hex-refused-token"}'


class Relay(BaseHTTPRequestHandler):
    """Its server has a `hex_dir`: where Hex keeps local-api.json."""

    timeout = 30  # seconds for the app to send a request: Hex allows an upload as long

    def do_GET(self) -> None:
        self.relay("GET")

    def do_POST(self) -> None:
        self.relay("POST")

    def relay(self, method: str) -> None:
        url = urlsplit(self.path)
        if (method, url.path) not in ROUTES:
            return self.reply(404, {"code": "not-found"})
        body = None
        if method == "POST":
            length = self.headers.get("Content-Length", "")
            if not length.isdigit():
                return self.reply(411, {"code": "length-required"})
            if int(length) > MAX_BODY:
                return self.reply(413, {"code": "audio-too-large"})
            body = self.rfile.read(int(length))
        query = urlencode([(k, v) for k, v in parse_qsl(url.query) if k in QUERY])
        path = f"{url.path}?{query}" if query else url.path
        content_type = self.headers.get("Content-Type", "application/octet-stream")
        try:
            status, kind, data = forward(self.server.hex_dir, method, path, body, content_type)  # type: ignore[attr-defined]
        except HexNotRunning:
            return self.reply(503, {"code": "hex-not-running"})
        except HexFileUnreadable:
            return self.reply(503, {"code": "hex-file-unreadable"})
        self.send(status, kind, data)

    def reply(self, status: int, body: dict[str, Any]) -> None:
        self.close_connection = True  # what's left of a refused upload isn't read
        self.send(status, "application/json", json.dumps(body).encode())

    def send(self, status: int, kind: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def address_string(self) -> str:
        return "app"  # a Unix socket's client has no address

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {format % args}\n")


def unix_server(socket: Path, hex_dir: Path) -> socketserver.BaseServer:
    socket.unlink(missing_ok=True)  # left by the last run
    umask = os.umask(0o177)  # the socket opens only for this user
    try:
        server = socketserver.ThreadingUnixStreamServer(str(socket), Relay)
    finally:
        os.umask(umask)
    server.daemon_threads = True
    server.hex_dir = hex_dir  # type: ignore[attr-defined]
    return server


def main() -> None:
    hex_dir = Path(os.environ.get("HEX_DIR", "~/.local/share/hex")).expanduser()
    socket = Path(os.environ.get("HEX_SOCKET", "/run/hex/relay.sock"))
    # `docker compose stop` sends SIGTERM: leave through `finally`, so the socket goes too and the app sees Hex is off.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        with unix_server(socket, hex_dir) as server:
            print(f"Passing dictation to Hex ({hex_dir / 'local-api.json'}) from {socket}", flush=True)
            server.serve_forever()
    finally:
        socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
