"""Session cookies you paste into the dashboard, for the sources that log in with your browser session (UW gives
students no other way into Canvas and Gradescope).

They're kept in data/cookies.json, which only the app's user can read: not in the database, whose snapshot the
agent reads, and never sent back by the API. A pasted cookie wins over the one config.yaml gives the source (from
.env) until that one changes; then you've put a newer cookie in .env, so it wins and the pasted one is forgotten.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
from datetime import datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, Field

log = logging.getLogger(__name__)

MAX_PASTE = 200_000  # a whole Copy as cURL, headers and all
MAX_COOKIE = 16_384
NOT_A_COOKIE = (
    "That doesn't look like a cookie. Paste the Cookie request header (name=value; name=value), "
    "the whole header line, or Copy as cURL."
)

_HEADER_LINE = re.compile(r"^[ \t]*cookie[ \t]*:[ \t]*(.*?)[ \t]*$", re.I | re.M)  # also among other headers
_FETCH_HEADER = re.compile(r'"cookie"\s*:\s*"((?:[^"\\]|\\.)*)"', re.I)  # Copy as fetch (Node.js)
_CURL_HEADER = re.compile(r"\s*cookie\s*:\s*(.*)", re.I | re.S)
_PAIR = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+=")
_PRINTABLE = re.compile(r"^[\x20-\x7e]*$")


def parse_cookie(text: str) -> str:
    """The Cookie header's value from what you copied out of the browser: the value itself, the header line
    (alone or among the other request headers), Copy as cURL (bash or cmd), or Copy as fetch (Node.js).

    Raises ValueError with a message that's safe to show: it never quotes what you pasted.
    """
    if len(text) > MAX_PASTE:
        raise ValueError("That's far longer than a cookie. Paste only the Cookie header, or one request's cURL.")
    text = text.replace("\r\n", "\n").strip().lstrip("\ufeff")
    if re.match(r"curl(\.exe)?\s", text):
        values = _from_curl(text)
    elif m := _FETCH_HEADER.search(text):
        try:
            values = [json.loads(f'"{m.group(1)}"')]
        except ValueError:
            raise ValueError(NOT_A_COOKIE) from None
    else:
        values = _HEADER_LINE.findall(text) or [_unquote(text)]
    return _clean("; ".join(values))


def _from_curl(text: str) -> list[str]:
    if '^"' in text:  # Copy as cURL (cmd): ^ escapes the next character, and one at a line's end continues it
        text = re.sub(r"\^(.)", r"\1", re.sub(r"\^\n", " ", text))
    try:
        args = shlex.split(re.sub(r"\\\n", " ", text))
    except ValueError:
        raise ValueError("That cURL command didn't parse. Copy it again, all of it.") from None
    values = []
    for flag, value in zip(args, args[1:]):
        if flag in ("-b", "--cookie"):
            values.append(value)
        elif flag in ("-H", "--header") and (m := _CURL_HEADER.fullmatch(value)):
            values.append(m.group(1))
    if not values:
        raise ValueError("That cURL command has no cookie. Copy it from a request to the site while you're logged in.")
    return values


def _unquote(text: str) -> str:
    if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _clean(value: str) -> str:
    parts = [p.strip() for p in value.split(";")]
    parts = [p for p in parts if p]
    if not parts or not any(_PAIR.match(p) for p in parts) or not all(_PRINTABLE.match(p) for p in parts):
        raise ValueError(NOT_A_COOKIE)
    cookie = "; ".join(parts)
    if len(cookie) > MAX_COOKIE:
        raise ValueError("That's longer than a browser sends as a cookie. Paste only the Cookie header.")
    return cookie


class SavedCookie(BaseModel):
    cookie: str = Field(repr=False)
    saved_at: AwareDatetime
    configured: str  # a hash of the cookie config.yaml (.env) gave the source when this one was pasted


class CookieStore:
    """Pasted cookies by source name, in a JSON file only the app's user can read."""

    def __init__(self, path: Path):
        self.path = path
        self._saved = self._load()

    def get(self, name: str) -> SavedCookie | None:
        return self._saved.get(name)

    def pasted(self, name: str, configured: str | None) -> str | None:
        """The pasted cookie to start a source with. None if there isn't one, or if config.yaml's cookie has changed
        since it was pasted: then that's the newer one, and the pasted one is forgotten."""
        saved = self._saved.get(name)
        if saved is None:
            return None
        if configured is not None and saved.configured != _digest(configured):
            log.info("%s: .env has a new cookie since one was pasted, so .env's is used", name)
            del self._saved[name]
            self._write()
            return None
        return saved.cookie

    def save(self, name: str, cookie: str, configured: str | None, now: datetime) -> SavedCookie:
        saved = SavedCookie(cookie=cookie, saved_at=now, configured=_digest(configured))
        self._saved[name] = saved
        self._write()
        return saved

    def _load(self) -> dict[str, SavedCookie]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {name: SavedCookie.model_validate(v) for name, v in raw.items()}
        except FileNotFoundError:
            return {}
        except (ValueError, AttributeError) as e:  # not str(e): a validation error quotes the cookie
            log.warning("ignoring %s: it isn't readable (%s)", self.path, type(e).__name__)
            return {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        data = json.dumps({name: s.model_dump(mode="json") for name, s in self._saved.items()}, indent=2)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, 0o600)  # in case an old .tmp was there with other permissions
        os.replace(tmp, self.path)


def _digest(cookie: str | None) -> str:
    return hashlib.sha256((cookie or "").encode()).hexdigest()
