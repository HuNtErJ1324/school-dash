"""The dashboard's side of the agent: asks the runner in the agent container (see runner.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import AgentConfig


class AgentError(Exception):
    """A failure whose message is safe and useful to show in the dashboard as-is."""


@dataclass
class AgentReply:
    text: str
    ops: list[dict[str, Any]] = field(default_factory=list)  # what the agent saved: plans, briefs
    model: str | None = None
    cost: float | None = None
    seconds: float | None = None
    tool_calls: int = 0


class AgentClient:
    def __init__(self, url: str | None, config: AgentConfig, http: httpx.AsyncClient):
        self.url = url.rstrip("/") if url else None
        self.config = config
        self.http = http

    @property
    def enabled(self) -> bool:
        return self.url is not None

    async def health(self) -> dict[str, Any]:
        """{"ok", "prime_agent", "api_key", "busy"} from the runner, or {"ok": False, "error"}."""
        if not self.url:
            return {"ok": False, "error": "No agent container: AGENT_URL isn't set."}
        try:
            resp = await self.http.get(f"{self.url}/health", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"ok": False, "error": f"The agent container isn't answering ({type(e).__name__})."}

    async def ask(self, thread: str, prompt: str, system: str) -> AgentReply:
        if not self.url:
            raise AgentError("No agent container: AGENT_URL isn't set.")
        body = {"thread": thread, "prompt": prompt, "system": system, "model": self.config.model,
                "thinking": self.config.thinking}  # fmt: skip
        try:
            # A request queues behind any other the runner is working on, so allow for two.
            resp = await self.http.post(f"{self.url}/ask", json=body, timeout=httpx.Timeout(5, read=1900))
        except httpx.HTTPError as e:
            raise AgentError(f"The agent container isn't answering ({type(e).__name__}).") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise AgentError(f"The agent container returned HTTP {resp.status_code}.") from e
        if not resp.is_success:
            raise AgentError(data.get("error") or f"The agent container returned HTTP {resp.status_code}.")
        return AgentReply(
            text=data.get("text") or "", ops=data.get("ops") or [], model=data.get("model"), cost=data.get("cost"),
            seconds=data.get("seconds"), tool_calls=data.get("tool_calls") or 0,
        )  # fmt: skip

    async def reset(self, thread: str) -> None:
        if self.url:
            try:
                await self.http.post(f"{self.url}/reset", json={"thread": thread}, timeout=10)
            except httpx.HTTPError:
                pass
