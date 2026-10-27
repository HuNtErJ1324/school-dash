"""Runs each source on its own interval and on demand, never two runs of one source at once."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from .models import SyncRun
from .sources.base import Source

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, sources: list[Source], run: Callable[[Source], Awaitable[SyncRun]]):
        self.sources = {s.name: s for s in sources}
        self._run = run
        self._locks = {name: asyncio.Lock() for name in self.sources}
        self._tasks: list[asyncio.Task[None]] = []
        self.next_run_at: dict[str, datetime] = {}

    def start(self, stagger_seconds: float = 2.0) -> None:
        for i, source in enumerate(self.sources.values()):
            task = asyncio.create_task(self._loop(source, i * stagger_seconds), name=f"sync:{source.name}")
            self._tasks.append(task)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def is_running(self, name: str) -> bool:
        return self._locks[name].locked()

    async def run_now(self, name: str) -> None:
        lock = self._locks[name]
        if lock.locked():  # already syncing: wait for that run rather than starting another
            async with lock:
                return
        async with lock:
            await self._run(self.sources[name])

    async def _loop(self, source: Source, delay: float) -> None:
        await asyncio.sleep(delay)
        while True:
            try:
                await self.run_now(source.name)
            except Exception:
                log.exception("scheduled sync of %s crashed", source.name)
            self.next_run_at[source.name] = datetime.now(timezone.utc) + source.interval
            await asyncio.sleep(source.interval.total_seconds())
