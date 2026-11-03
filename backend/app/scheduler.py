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
        self._started = False
        self.next_run_at: dict[str, datetime] = {}

    def start(self, stagger_seconds: float = 2.0) -> None:
        self._started = True
        for i, source in enumerate(self.sources.values()):
            self._start_loop(source, i * stagger_seconds)

    def add(self, source: Source) -> None:
        """A source that couldn't start with the others (it had no login yet). Runs on its interval from now on."""
        self.sources[source.name] = source
        self._locks.setdefault(source.name, asyncio.Lock())
        if self._started:
            self.next_run_at[source.name] = datetime.now(timezone.utc) + source.interval
            self._start_loop(source, source.interval.total_seconds())  # whoever added it syncs it now

    def _start_loop(self, source: Source, delay: float) -> None:
        self._tasks.append(asyncio.create_task(self._loop(source, delay), name=f"sync:{source.name}"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def is_running(self, name: str) -> bool:
        return self._locks[name].locked()

    async def run_now(self, name: str, again: bool = False) -> None:
        """Sync a source now. If it's already syncing, wait for that run, and with `again` (it started before a change
        that matters, like a new cookie) sync once more after it."""
        lock = self._locks[name]
        if lock.locked() and not again:  # wait for that run rather than starting another
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
