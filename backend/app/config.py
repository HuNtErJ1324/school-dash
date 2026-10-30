"""Settings from the environment, plus config.yaml (courses and sources)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingEnvError(ValueError):
    def __init__(self, var: str):
        super().__init__(f"environment variable {var} is not set (referenced as ${{{var}}})")
        self.var = var


def expand_env(value: Any) -> Any:
    """Replace ${VAR} references in strings, recursively, so secrets can live in .env."""
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            var = m.group(1)
            if var not in os.environ:
                raise MissingEnvError(var)
            return os.environ[var]

        return _ENV_REF.sub(sub, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


class GradeCategory(BaseModel):
    name: str  # "Labs"; also matched against a source's own grouping (a Canvas assignment group)
    weight: float  # share of the course grade, in any units (percent is easiest)
    match: str | None = None  # regex tried on item titles; defaults to the name ("Labs" -> lab, labs)
    drop_lowest: int = 0


class Grading(BaseModel):
    categories: list[GradeCategory]


class CourseConfig(BaseModel):
    code: str  # "CSE 410"
    name: str | None = None
    color: str | None = None
    canvas_id: int | None = None
    quarter: str | None = Field(None, pattern=r"^\d{2}(wi|sp|su|au)$")  # "26au"; sources usually know it
    links: dict[str, str] = Field(default_factory=dict)
    grading: Grading | None = None  # the syllabus's weights, for grade estimates


class SourceConfig(BaseModel):
    """Fields every source shares. The rest is validated by that source type's own Config model."""

    model_config = ConfigDict(extra="allow")

    type: str
    name: str | None = None
    enabled: bool = True
    interval_minutes: float | None = None

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class SiteConfig(BaseModel):
    """A course website to read documents from: its pages, the PDFs they link, shared Google slides."""

    course: str  # "CSE 410"
    url: str  # only pages under this address are read
    max_pages: int = 200
    skip: list[str] = Field(default_factory=list)  # regexes on URLs to leave out


class LibraryConfig(BaseModel):
    sites: list[SiteConfig] = Field(default_factory=list)
    canvas: bool = True  # read syllabi, assignment descriptions, pages and files with the canvas source's login
    interval_hours: float = 12
    max_file_mb: float = 60


class AgentConfig(BaseModel):
    model: str = "claude-opus-5-5"
    thinking: str = "medium"  # prime-agent's --thinking level
    # "provider/model" that answers when the Claude API is out of credit (or has no key): openai-codex/... is
    # a ChatGPT Plus/Pro subscription, used once prime-agent is signed in to it (README). "" for none.
    backup: str = Field("openai-codex/gpt-6.1-sol", pattern=r"^$|^[\w.-]+/[\w./-]+$")


class PlannerConfig(BaseModel):
    time: str = Field("06:30", pattern=r"^\d{1,2}:\d{2}$")  # when the day's plan is made, local time
    # Hours of focused work you usually have, by weekday (mon..sun); `default` for the rest.
    hours: dict[str, float] = Field(default_factory=lambda: {"default": 4})
    notes: str = ""  # anything the planner should always know ("I work Tue/Thu 5-9pm")
    brief_days: float = 10  # write briefs for assignments due within this many days
    max_briefs: int = 4  # per morning, so a new quarter doesn't spend a lot at once


class AppConfig(BaseModel):
    quarter: str | None = None  # unused: the dashboard works out the quarter (quarters.py)
    timezone: str = "America/Los_Angeles"
    courses: list[CourseConfig] = Field(default_factory=list)
    sources: list[SourceConfig] = Field(default_factory=list)
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)


def load_config(path: Path) -> AppConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Sources expand ${VAR} lazily (see sources.build_sources) so one missing secret
    # breaks only that source instead of the whole dashboard.
    sources = data.pop("sources", None) or []
    config = AppConfig.model_validate(expand_env(data))
    config.sources = [SourceConfig.model_validate(s) for s in sources]
    return config


class Settings(BaseModel):
    config_path: Path = Path("config.yaml")
    data_dir: Path = Path("data")
    static_dir: Path | None = None
    sync_on_start: bool = True
    agent_url: str | None = None  # the agent container's runner (docker compose sets http://agent:8790)

    @classmethod
    def from_env(cls) -> Settings:
        static = os.environ.get("STATIC_DIR")
        return cls(
            config_path=Path(os.environ.get("CONFIG_PATH", "config.yaml")),
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
            static_dir=Path(static) if static else None,
            sync_on_start=os.environ.get("SYNC_ON_START", "1").lower() not in ("0", "false", "no"),
            agent_url=os.environ.get("AGENT_URL") or None,
        )
