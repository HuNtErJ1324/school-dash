"""The agent's jobs, run in the background so pages can poll them: the morning's briefs and plan,
re-plans, briefs on demand, and answers in conversations. One job talks to the agent at a time."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import AppConfig
from ..grades import course_grades
from ..models import Brief, Conversation, Course, Document, Item, Message, Plan
from ..views import ItemOut, live_items, merged_items
from . import prompts
from .client import AgentClient, AgentError, AgentReply
from .context import is_open, items_between, latest_plans, ready_briefs, write_snapshot
from .schemas import PlanTask, Resource

log = logging.getLogger(__name__)
BRIEF_KINDS = {"assignment", "exam"}


class Assistant:
    def __init__(
        self,
        client: AgentClient,
        Session: sessionmaker[Session],
        config: AppConfig,
        db_path: Path,
        snapshot_dir: Path,
        configured: Callable[[], list[str]],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.client = client
        self.Session = Session
        self.config = config
        self.db_path = db_path
        self.snapshot_dir = snapshot_dir
        self.configured = configured
        self.now = now
        self.tz = ZoneInfo(config.timezone)
        self.lock = asyncio.Lock()
        self.next_morning_at: datetime | None = None
        self._jobs: set[asyncio.Task[None]] = set()
        self._loop_task: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()  # made in the app's lifespan; jobs run on this loop

    # -- lifecycle -------------------------------------------------------------------------------

    def start(self) -> None:
        self.recover()
        if self.client.enabled:
            self._loop_task = asyncio.create_task(self._mornings(), name="agent:mornings")

    async def stop(self) -> None:
        tasks = [t for t in [self._loop_task, *self._jobs] if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def recover(self) -> None:
        """Jobs that were running when the server stopped won't finish now."""
        now = self.now()
        with self.Session.begin() as s:
            for model in (Plan, Brief):
                for row in s.scalars(select(model).where(model.status == "running")):
                    row.status, row.error, row.finished_at = "error", "Interrupted by a restart.", now
            for m in s.scalars(select(Message).where(Message.status == "running")):
                m.status, m.error = "error", "Interrupted by a restart."

    def today(self) -> str:
        return self.now().astimezone(self.tz).date().isoformat()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a job on the server's event loop, from it or from a sync endpoint's worker thread."""

        def create() -> None:
            task = self._loop.create_task(coro)
            self._jobs.add(task)
            task.add_done_callback(self._jobs.discard)

        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if here is self._loop:
            create()
        else:
            self._loop.call_soon_threadsafe(create)

    async def _ask(self, thread: str, prompt: str) -> AgentReply:
        async with self.lock:
            now = self.now()
            await asyncio.to_thread(
                write_snapshot, self.Session, self.db_path, self.snapshot_dir, self.configured(), self.config, now
            )  # fmt: skip
            return await self.client.ask(thread, prompt, prompts.system(now, self.tz))

    async def status(self) -> dict[str, Any]:
        health = await self.client.health()
        start = self.now().astimezone(self.tz).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self.Session() as s:
            spent = sum(
                s.scalar(select(func.coalesce(func.sum(m.cost), 0)).where(m.created_at >= start)) or 0
                for m in (Plan, Brief, Message)
            )  # fmt: skip
        return {
            "enabled": self.client.enabled,
            "ready": usable(health, self.config.agent.backup),
            "problem": problem(health, self.config.agent.backup) if self.client.enabled else None,
            "model": self.config.agent.model,
            "busy": self.lock.locked() or bool(health.get("busy")),
            "next_plan_at": self.next_morning() if self.client.enabled else None,
            "spent_this_month": round(spent, 2),
        }

    # -- items -----------------------------------------------------------------------------------

    def item(self, s: Session, item_id: int) -> ItemOut | None:
        row = s.get(Item, item_id)
        if row is None:
            return None
        group = row.group_id or row.id
        rows = s.scalars(live_items(self.configured()).where(or_(Item.id == item_id, Item.group_id == group))).unique().all()
        merged = merged_items(s, rows, self.configured())
        return merged[0] if merged else None

    def _items(self, s: Session) -> list[ItemOut]:
        now = self.now()
        return items_between(s, self.configured(), now - timedelta(days=14), now + timedelta(days=120))

    # -- plans -----------------------------------------------------------------------------------

    def new_plan(self, note: str | None) -> int:
        with self.Session.begin() as s:
            plan = Plan(date=self.today(), status="running", note=note, tasks=[], heads_up=[], created_at=self.now())
            s.add(plan)
            s.flush()
            return plan.id

    def start_plan(self, note: str | None) -> int:
        plan_id = self.new_plan(note)
        self._spawn(self.run_plan(plan_id))
        return plan_id

    async def run_plan(self, plan_id: int) -> None:
        try:
            date, prompt = await asyncio.to_thread(self._plan_prompt, plan_id)
            reply = await self._ask(f"plan:{date}", prompt)
            await asyncio.to_thread(self._finish_plan, plan_id, reply)
        except Exception as e:
            self._fail(Plan, plan_id, e)

    def _plan_prompt(self, plan_id: int) -> tuple[str, str]:
        with self.Session() as s:
            plan = s.get(Plan, plan_id)
            items = self._items(s)
            courses = {c.id: c for c in s.scalars(select(Course))}
            rows = s.scalars(live_items(self.configured()).where(Item.course_id.is_not(None))).unique().all()
            grades = course_grades(merged_items(s, rows, self.configured()), courses, self.config.courses)
            previous = next(iter(latest_plans(s, before=plan.date, limit=1)), None)
            done_today = [
                t["title"] for p in s.scalars(select(Plan).where(Plan.date == plan.date, Plan.status == "ready"))
                for t in p.tasks if t.get("done")
            ]  # fmt: skip
            prompt = prompts.plan_prompt(self.now(), self.tz, self.config.planner, items, set(ready_briefs(s)), grades,
                                         previous, list(dict.fromkeys(done_today)), plan.note)  # fmt: skip
            return plan.date, prompt

    def _finish_plan(self, plan_id: int, reply: AgentReply) -> None:
        with self.Session.begin() as s:
            self._save_briefs(s, reply)
            plan = s.get(Plan, plan_id)
            _record(plan, reply, self.now())
            op = next((o for o in reversed(reply.ops) if o.get("op") == "plan"), None)
            if op is None:
                plan.status, plan.error = "error", "The agent finished without saving a plan."
                return
            try:
                tasks = [PlanTask.model_validate(t).model_dump() for t in op.get("tasks") or []]
            except ValidationError as e:
                plan.status, plan.error = "error", f"The agent's plan didn't fit the format: {e.errors()[0]['msg']}"
                return
            plan.summary, plan.tasks = op.get("summary") or None, tasks
            plan.heads_up = [str(h) for h in op.get("heads_up") or []]
            plan.status = "ready"

    def set_task_done(self, plan_id: int, index: int, done: bool) -> Plan | None:
        with self.Session.begin() as s:
            plan = s.get(Plan, plan_id)
            if plan is None or not 0 <= index < len(plan.tasks):
                return None
            tasks = [dict(t) for t in plan.tasks]
            tasks[index]["done"] = done
            plan.tasks = tasks  # a new list, so the JSON column is saved
            return plan

    # -- briefs ----------------------------------------------------------------------------------

    def new_brief(self, item_id: int) -> int:
        with self.Session.begin() as s:
            item = self.item(s, item_id)
            if item is None:
                raise LookupError(f"no item {item_id}")
            title = f"{item.course.code} · {item.title}" if item.course else item.title
            brief = Brief(item_id=item.id, title=title, status="running", resources=[], created_at=self.now())
            s.add(brief)
            s.flush()
            return brief.id

    def start_brief(self, item_id: int) -> int:
        brief_id = self.new_brief(item_id)
        self._spawn(self.run_brief(brief_id))
        return brief_id

    async def run_brief(self, brief_id: int) -> None:
        try:
            item_id, prompt = await asyncio.to_thread(self._brief_prompt, brief_id)
            reply = await self._ask(f"brief:{item_id}", prompt)
            await asyncio.to_thread(self._finish_brief, brief_id, reply)
        except Exception as e:
            self._fail(Brief, brief_id, e)

    def _brief_prompt(self, brief_id: int) -> tuple[int, str]:
        with self.Session() as s:
            brief = s.get(Brief, brief_id)
            item = self.item(s, brief.item_id)
            if item is None:
                raise AgentError("That item isn't on the dashboard anymore.")
            ids = s.scalars(select(Item.source_id).where(or_(Item.id == item.id, Item.group_id == item.id))).all()
            docs = s.scalars(
                select(Document).where(Document.item_source_id.in_(ids), Document.removed_at.is_(None))
            ).all()  # fmt: skip
            return item.id, prompts.brief_prompt(item, self.tz, docs)

    def _finish_brief(self, brief_id: int, reply: AgentReply) -> None:
        with self.Session.begin() as s:
            brief = s.get(Brief, brief_id)
            saved = self._save_briefs(s, reply, into=brief)
            _record(brief, reply, self.now())
            if brief.id not in saved:
                brief.status, brief.error = "error", "The agent finished without saving the brief."

    def _save_briefs(self, s: Session, reply: AgentReply, into: Brief | None = None) -> set[int]:
        """Store every brief the agent saved. One for `into`'s item fills `into`; others become new briefs."""
        saved: set[int] = set()
        for op in reply.ops:
            if op.get("op") != "brief":
                continue
            row = s.get(Item, int(op.get("item_id") or 0))
            if row is None:
                log.warning("the agent saved a brief for unknown item %s", op.get("item_id"))
                continue
            group = row.group_id or row.id
            if into is not None and into.item_id == group:
                brief = into
            else:
                item = self.item(s, group)
                title = (f"{item.course.code} · {item.title}" if item and item.course else row.title)
                brief = Brief(item_id=group, title=title, created_at=self.now())
                s.add(brief)
            brief.markdown = op.get("markdown")
            brief.minutes = op.get("minutes")
            brief.resources = [Resource.model_validate(r).model_dump() for r in op.get("resources") or []]
            brief.status, brief.error, brief.finished_at = "ready", None, self.now()
            s.flush()
            saved.add(brief.id)
        return saved

    def briefs_wanted(self) -> list[int]:
        """Upcoming assignments and exams without a brief, soonest first."""
        now, horizon = self.now(), timedelta(days=self.config.planner.brief_days)
        with self.Session() as s:
            have = set(s.scalars(select(Brief.item_id).where(Brief.status.in_(("ready", "running")))))
            items = [
                i for i in self._items(s)
                if i.kind in BRIEF_KINDS and is_open(i) and i.at and now <= i.at <= now + horizon and i.id not in have
            ]  # fmt: skip
        items.sort(key=lambda i: i.at)
        return [i.id for i in items[: self.config.planner.max_briefs]]

    # -- conversations ---------------------------------------------------------------------------

    def start_conversation(
        self, text: str, item_id: int | None, course_id: str | None = None, mode: str = "ask",
        photos: list[str] | None = None, canvas: dict[str, Any] | None = None,
    ) -> int:  # fmt: skip
        now = self.now()
        with self.Session.begin() as s:
            if item_id is not None and course_id is None:
                item = self.item(s, item_id)
                course_id = item.course.id if item and item.course else None
            conv = Conversation(title=" ".join(text.split())[:80], item_id=item_id, course_id=course_id, mode=mode,
                                created_at=now, updated_at=now)  # fmt: skip
            s.add(conv)
            s.flush()
            conv_id = conv.id
        self.add_message(conv_id, text, photos, canvas)
        return conv_id

    def add_message(
        self, conv_id: int, text: str, photos: list[str] | None = None, canvas: dict[str, Any] | None = None
    ) -> int:
        now = self.now()
        with self.Session.begin() as s:
            conv = s.get(Conversation, conv_id)
            if conv is None:
                raise LookupError(f"no conversation {conv_id}")
            if s.scalar(select(Message.id).where(Message.conversation_id == conv_id, Message.status == "running")):
                raise RuntimeError("The agent is still answering the last message.")
            s.add(Message(conversation_id=conv_id, role="user", status="ready", text=text, photos=photos or None,
                          canvas=canvas, created_at=now))  # fmt: skip
            answer = Message(conversation_id=conv_id, role="assistant", status="running", text="", created_at=now)
            s.add(answer)
            conv.updated_at = now
            s.flush()
            answer_id = answer.id
        self._spawn(self.run_answer(answer_id))
        return answer_id

    async def run_answer(self, message_id: int) -> None:
        try:
            with self.Session() as s:
                answer = s.get(Message, message_id)
                conv = s.get(Conversation, answer.conversation_id)
                users = s.scalars(
                    select(Message).where(Message.conversation_id == conv.id, Message.role == "user").order_by(Message.id)
                ).all()  # fmt: skip
                item = self.item(s, conv.item_id) if conv.item_id else None
                course = s.get(Course, conv.course_id) if conv.course_id else None
                last, first, code = users[-1], len(users) == 1, course.code if course else None
                if conv.mode == "canvas":
                    prompt = prompts.canvas_prompt(last.text, last.canvas or {}, last.photos or [], first, code)
                elif last.canvas:  # a Tutor session, from the canvas beside it
                    prompt = prompts.session_canvas_prompt(last.text, last.canvas, last.photos or [], item, self.tz,
                                                           first, conv.mode, code)  # fmt: skip
                else:
                    prompt = prompts.chat_prompt(last.text, item, self.tz, first=first, mode=conv.mode, course=code)
                    prompt = prompts.with_photos(prompt, last.photos or [])
            reply = await self._ask(f"chat:{conv.id}", prompt)
            with self.Session.begin() as s:
                self._save_briefs(s, reply)
                answer = s.get(Message, message_id)
                answer.text, answer.cost, answer.status = reply.text or "(no answer)", reply.cost, "ready"
        except Exception as e:
            self._fail(Message, message_id, e)

    # -- mornings --------------------------------------------------------------------------------

    async def morning(self) -> None:
        health = await self.client.health()
        if not usable(health, self.config.agent.backup):
            log.info("no morning plan: %s", problem(health, self.config.agent.backup))
            return
        for item_id in await asyncio.to_thread(self.briefs_wanted):
            await self.run_brief(self.new_brief(item_id))
        await self.run_plan(self.new_plan(None))

    def _plan_time(self, day: datetime) -> datetime:
        h, m = (int(x) for x in self.config.planner.time.split(":"))
        return datetime.combine(day.date(), time(h, m), tzinfo=self.tz)

    def next_morning(self) -> datetime:
        local = self.now().astimezone(self.tz)
        at = self._plan_time(local)
        return at if at > local else self._plan_time(local + timedelta(days=1))

    def missed_today(self) -> bool:
        """Past today's planning time with no plan made (the server was down, or just started)."""
        if self.now().astimezone(self.tz) < self._plan_time(self.now().astimezone(self.tz)):
            return False
        with self.Session() as s:
            return s.scalar(select(Plan.id).where(Plan.date == self.today()).limit(1)) is None

    async def _mornings(self) -> None:
        await asyncio.sleep(120)  # let the first syncs land
        while True:
            if self.missed_today():
                try:
                    await self.morning()
                except Exception:
                    log.exception("the morning plan crashed")
            self.next_morning_at = self.next_morning()
            await asyncio.sleep(max(1.0, (self.next_morning_at - self.now()).total_seconds()))

    def _fail(self, model: type[Plan] | type[Brief] | type[Message], row_id: int, e: Exception) -> None:
        if not isinstance(e, AgentError):
            log.exception("agent job failed")
        message = str(e) if isinstance(e, AgentError) else f"{type(e).__name__}: {e}"
        with self.Session.begin() as s:
            row = s.get(model, row_id)
            if row is not None:
                row.status, row.error = "error", message
                if hasattr(row, "finished_at"):
                    row.finished_at = self.now()


def _record(row: Plan | Brief, reply: AgentReply, now: datetime) -> None:
    row.answer, row.model, row.cost, row.seconds, row.finished_at = reply.text, reply.model, reply.cost, reply.seconds, now


def usable(health: dict[str, Any], backup: str = "") -> bool:
    return problem(health, backup) is None


def problem(health: dict[str, Any], backup: str = "") -> str | None:
    if not health.get("ok"):
        return health.get("error") or "The agent container isn't answering."
    if not health.get("prime_agent"):
        return "prime-agent isn't installed in the agent container: set PRIME_AGENT_DIR in .env."
    if not health.get("api_key") and not (backup and backup.split("/")[0] in (health.get("logins") or [])):
        return "Add your Claude API key to .env as ANTHROPIC_API_KEY, then restart the stack."
    return None
