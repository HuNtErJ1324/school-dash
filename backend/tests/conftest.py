from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.sources.base import FetchResult, Source, SourceContext

FIXTURES = Path(__file__).parent / "fixtures"
LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=LA)


def fetch(
    source: Source,
    now: datetime = NOW,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> FetchResult:
    """Run source.fetch at a fixed time, optionally against a fake HTTP server."""

    async def go() -> FetchResult:
        transport = httpx.MockTransport(handler) if handler else None
        async with httpx.AsyncClient(transport=transport) as http:
            return await source.fetch(SourceContext(tz=LA, http=http, now=lambda: now))

    return asyncio.run(go())


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
