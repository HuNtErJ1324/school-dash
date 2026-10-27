"""Source adapters. A new source is a module here with a Source subclass decorated @register."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from pydantic import ValidationError

from ..config import MissingEnvError, SourceConfig, expand_env
from . import canvas, canvas_feed, cse_calendar, demo, gradescope, ics  # noqa: F401  (importing registers them)
from .base import REGISTRY, Source

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass
class ConfigProblem:
    """A configured source that couldn't be built; shown in sync health instead of crashing."""

    name: str
    type: str
    error: str


def build_sources(configs: list[SourceConfig]) -> tuple[list[Source], list[ConfigProblem]]:
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

        cls = REGISTRY.get(sc.type)
        if cls is None:
            problems.append(ConfigProblem(name, sc.type, f"unknown type; known types: {', '.join(sorted(REGISTRY))}"))
            continue
        try:
            config = cls.Config.model_validate(expand_env(sc.options))
        except MissingEnvError as e:
            problems.append(ConfigProblem(name, sc.type, str(e)))
            continue
        except ValidationError as e:
            # Not str(e): pydantic echoes input values, and those can be secrets.
            msg = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'config'}: {err['msg']}" for err in e.errors())
            problems.append(ConfigProblem(name, sc.type, msg))
            continue

        interval = timedelta(minutes=sc.interval_minutes) if sc.interval_minutes else None
        sources.append(cls(name, config, interval))
    return sources, problems
