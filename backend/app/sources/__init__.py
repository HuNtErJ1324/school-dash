"""Source adapters. A new source is a module here with a Source subclass decorated @register."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from pydantic import ValidationError

from ..config import MissingEnvError, SourceConfig, expand_env
from . import canvas, canvas_feed, cse_calendar, demo, gradescope, ics  # noqa: F401  (importing registers them)
from .base import REGISTRY, Source

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PLACEHOLDER_COOKIE = "placeholder=1"  # any cookie, to see whether a source would start with one


@dataclass
class ConfigProblem:
    """A configured source that couldn't be built; shown in sync health instead of crashing."""

    name: str
    type: str
    error: str
    needs_login: bool = False  # only its cookie is missing or empty, so pasting one starts it


class SourceConfigError(ValueError):
    """A source's config doesn't work; the message is safe to show (it never quotes values)."""


def build_source(sc: SourceConfig, name: str, cookie: str | None = None) -> Source:
    """One source from config.yaml. `cookie` (one you pasted) replaces the cookie the config gives it."""
    cls = REGISTRY.get(sc.type)
    if cls is None:
        raise SourceConfigError(f"unknown type; known types: {', '.join(sorted(REGISTRY))}")
    options = sc.options
    if cookie is not None:
        options.pop("cookie", None)  # so a missing ${CANVAS_COOKIE} doesn't matter
    try:
        expanded = expand_env(options)
    except MissingEnvError as e:
        raise SourceConfigError(str(e)) from None
    if cookie is not None:
        expanded["cookie"] = cookie  # after expanding: a cookie is never read for ${VAR}s
    try:
        config = cls.Config.model_validate(expanded)
    except ValidationError as e:
        # Not str(e): pydantic echoes input values, and those can be secrets.
        msg = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'config'}: {err['msg']}" for err in e.errors())
        raise SourceConfigError(msg) from None
    interval = timedelta(minutes=sc.interval_minutes) if sc.interval_minutes else None
    return cls(name, config, interval)


def build_sources(
    configs: list[SourceConfig],
    pasted: Callable[[str, str | None], str | None] | None = None,  # (name, configured cookie) -> the pasted one
) -> tuple[list[Source], list[ConfigProblem]]:
    sources: list[Source] = []
    problems: list[ConfigProblem] = []
    names: set[str] = set()

    for i, sc in enumerate(configs, start=1):
        if not sc.enabled:
            continue
        name = sc.name or sc.type
        if not _NAME.match(name):
            problems.append(ConfigProblem(f"source-{i}", sc.type, f"invalid name {name!r}: use a-z, 0-9, - and _"))
            continue
        if name in names:
            problems.append(ConfigProblem(f"{name}-{i}", sc.type, f"another source is already named {name!r}"))
            continue
        names.add(name)

        login = logs_in_with_cookie(sc)
        cookie = pasted(name, configured_cookie(sc)) if pasted and login else None
        try:
            sources.append(build_source(sc, name, cookie))
        except SourceConfigError as e:
            needs_login = login and _with_any_cookie(sc, name) is not None
            problems.append(ConfigProblem(name, sc.type, str(e), needs_login=needs_login))
    return sources, problems


def logs_in_with_cookie(sc: SourceConfig) -> bool:
    """Whether the source logs in with a browser session's Cookie header (Canvas and Gradescope at UW)."""
    cls = REGISTRY.get(sc.type)
    return cls is not None and "cookie" in cls.Config.model_fields and "cookie" in sc.options


def cookie_logins(configs: list[SourceConfig]) -> dict[str, SourceConfig]:
    """The sources build_sources names that log in with a cookie, by name."""
    out: dict[str, SourceConfig] = {}
    for sc in configs:
        name = sc.name or sc.type
        if sc.enabled and _NAME.match(name) and logs_in_with_cookie(sc):
            out.setdefault(name, sc)  # a second source of the same name isn't started
    return out


def configured_cookie(sc: SourceConfig) -> str | None:
    """The cookie config.yaml gives a source (usually ${CANVAS_COOKIE} from .env); None if it's unset."""
    try:
        value = expand_env(sc.options.get("cookie"))
    except MissingEnvError:
        return None
    return value if isinstance(value, str) and value else None


def login_site(sc: SourceConfig, name: str) -> str | None:
    """Where a source logs in ("canvas.uw.edu"): where to copy its cookie from."""
    source = _with_any_cookie(sc, name)
    if source is None:
        return None
    return urlsplit(getattr(source.config, "base_url", "") or "").netloc or None


def _with_any_cookie(sc: SourceConfig, name: str) -> Source | None:
    """The source as it would be with some cookie; None if something besides the cookie is wrong."""
    try:
        return build_source(sc, name, _PLACEHOLDER_COOKIE)
    except SourceConfigError:
        return None
