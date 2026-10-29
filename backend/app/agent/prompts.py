"""What the dashboard asks the agent. The workspace's AGENTS.md (runner.py) holds the standing instructions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import PlannerConfig
from ..grades import CourseGrades
from ..models import Document, Plan
from ..views import ItemOut
from .context import SCHEDULE, is_open

_STATUS = {"pending": "not submitted", "missing": "MISSING", "submitted": "submitted", "graded": "graded"}


def system(now: datetime, tz: ZoneInfo) -> str:
    return (
        "You are the study planner and tutor in a UW student's personal school dashboard. Your workspace's "
        "AGENTS.md explains the data, the course library, the school.py helpers and the rules: read it at the "
        "start of every new session and follow it, above all its academic-integrity rules. "
        f"It is now {_when(now, tz)}, {now.astimezone(tz).year} ({tz.key} time)."
    )


def _when(dt: datetime | None, tz: ZoneInfo, all_day: bool = False) -> str:
    if dt is None:
        return "no date"
    d = dt.astimezone(tz)
    day = f"{d:%a %b} {d.day}"
    return day if all_day else f"{day}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def item_line(i: ItemOut, tz: ZoneInfo, briefs: set[int]) -> str:
    course = i.course.code if i.course else "no course"
    parts = [f"[item {i.id}] {course} · {i.title} ({i.kind})"]
    if i.kind == "exam" and i.starts_at:
        parts.append(_when(i.starts_at, tz, i.all_day))
    else:
        parts.append("due " + _when(i.due_at or i.at, tz, i.all_day))
    if i.points:
        parts.append(f"{i.points:g} pts")
    if i.status in _STATUS:
        parts.append(_STATUS[i.status])
    if i.pinned:
        parts.append("pinned")
    if i.id in briefs:
        parts.append("brief written")
    if i.note:
        parts.append(f'student\'s note: "{i.note}"')
    return "- " + " · ".join(parts)


def hours_for(planner: PlannerConfig, day: datetime) -> float:
    key = f"{day:%a}".lower()
    return planner.hours.get(key, planner.hours.get("default", 4))


def plan_prompt(
    now: datetime,
    tz: ZoneInfo,
    planner: PlannerConfig,
    items: Sequence[ItemOut],
    briefs: set[int],
    grades: Sequence[CourseGrades],
    previous: Plan | None,
    done_today: Sequence[str],
    note: str | None,
) -> str:
    today = now.astimezone(tz)
    upcoming = [i for i in items if is_open(i) and i.at and now <= i.at <= now + timedelta(days=21)]
    overdue = [i for i in items if is_open(i) and i.at and now - timedelta(days=14) <= i.at < now]
    schedule = [
        i for i in items
        if i.kind in SCHEDULE and i.starts_at and i.starts_at.astimezone(tz).date() == today.date()
    ]  # fmt: skip
    lines = [
        f"Make my plan for today, {today:%A} {today.date().isoformat()}. It's {_when(now, tz).split(', ')[1]} now.",
        "",
        f"Time I usually have on a {today:%A}: about {hours_for(planner, today):g} hours of focused work.",
    ]
    if planner.notes.strip():
        lines.append(f"What I've told you about my schedule: {planner.notes.strip()}")
    if note:
        lines += ["", f"For this plan specifically: {note.strip()}"]
    lines += ["", "Open work due in the next three weeks (the ids are context.json's):"]
    lines += [item_line(i, tz, briefs) for i in upcoming] or ["- nothing"]
    if overdue:
        lines += ["", "Past due and not marked done or submitted (some may be done; the sources can't always tell):"]
        lines += [item_line(i, tz, briefs) for i in overdue]
    lines += ["", "Today's schedule:"]
    for i in sorted(schedule, key=lambda i: i.starts_at):
        start, end = i.starts_at.astimezone(tz), i.ends_at.astimezone(tz) if i.ends_at else None
        span = f"{start:%H:%M}" + (f"-{end:%H:%M}" if end else "")
        where = f" ({i.location})" if i.location else ""
        lines.append(f"- {span} {i.course.code if i.course else ''} {i.kind.replace('_', ' ')}: {i.title}{where}")
    if not schedule:
        lines.append("- nothing scheduled")
    estimates = [
        f"{g.course.code} {g.estimate:.1f}% (on {g.counted_weight:g}% of the course grade so far)"
        for g in grades if g.estimate is not None
    ]  # fmt: skip
    if estimates:
        lines += ["", "Grade estimates: " + "; ".join(estimates) + "."]
    if previous:
        lines += ["", f"My plan from {previous.date}:"]
        lines += [f"- [{'x' if t.get('done') else ' '}] {t.get('title')}" for t in previous.tasks]
    if done_today:
        lines += ["", "Already done today (from earlier plans):"] + [f"- {t}" for t in done_today]
    lines += [
        "",
        "How to plan:",
        "1. Before estimating anything, find out what it involves: its brief if there is one, otherwise its spec "
        "or description in the library (INDEX.md, school.search). Labs and projects take several sessions; "
        "exams need review spread over days.",
        "2. Choose today's work so nothing turns into a crunch: work back from each due date, put design docs and "
        "other early milestones first, and leave slack before deadlines.",
        "3. Fit today's total to my time. Two to six tasks, each 25 to 120 minutes, each a concrete step I can "
        "start on right away. Use lectures and office hours on today's schedule where they help.",
        "4. Give each task the resources that help most: the spec section, the lecture slides, the textbook "
        "pages, by library doc id.",
        "5. If an upcoming assignment due within ten days has no brief and you read its materials anyway, save a "
        "brief for it too.",
        "6. Save the plan with school.save_plan, then reply with one or two sentences about the day.",
    ]
    return "\n".join(lines)


def brief_prompt(item: ItemOut, tz: ZoneInfo, docs: Sequence[Document]) -> str:
    lines = [f"Write a brief for this {item.kind}:", item_line(item, tz, set())]
    if item.url:
        lines.append(f"  Link: {item.url}")
    if docs:
        lines += ["", "Library documents tied to it:"]
        lines += [f"- doc {d.id}: {d.title} ({d.kind}, {d.text_path or 'no text version'})" for d in docs]
    else:
        lines += ["", "No library document is tied to it yet. Look for its spec or description with school.search "
                  "(by its name and number) and in INDEX.md. If it isn't there, say so in the brief and where it "
                  "probably lives, and write what you can from the syllabus, calendar and lectures."]  # fmt: skip
    lines += [
        "",
        "The brief helps me start and pace the work. It is not a solution. In markdown, with these sections:",
        "- **What it asks**: the deliverables and how they're graded and submitted, in a few bullets, in your words.",
        "- **Know first**: the concepts it relies on, each with where it's covered (doc links with pages).",
        "- **Ready check**: two or three quick questions I should be able to answer before starting (definitions, "
        "a small case), each with where to read up if I can't. Not the assignment's own problems.",
        "- **Plan of attack**: three to seven ordered steps for doing the work (read, set up, attempt, check, "
        "submit) with time estimates, ending at submission. The steps say what to do and where to look, never "
        "how to solve it: don't break the problems themselves into steps. Say which parts usually take longest.",
        "- **Watch out for**: requirements that are easy to miss and things to check before submitting.",
        "- **Ask about**: anything unclear in the spec that's worth asking in office hours or on Ed.",
        "",
        f"Save it with school.save_brief({item.id}, markdown, minutes=<your estimate for the whole thing>, "
        "resources=[...]), then reply with one sentence.",
    ]
    return "\n".join(lines)


# How a tutoring session from the Tutor page should run. "ask" is a plain question. Each mode leans on the
# scaffold/rigor dial in AGENTS.md (after TutorMoments, Zhang et al. 2026): find out what the student can
# already do before helping, support only what they can't, and raise the bar once they've got it.
MODES: dict[str, tuple[str, str | None]] = {
    "ask": ("Ask", None),
    "explain": ("Explain", "Help me understand this. Start by finding out what I already know: ask me to explain "
                "it or try a small case, and wait. Build from my answer, supporting only the parts I can't do "
                "yet, and once I've got a piece, say so and have me use it on my own before moving on. When I've "
                "got what I asked about, sum up what I learned and stop."),
    "quiz": ("Quiz me", "Quiz me on this. Ask one question at a time, taken from or modeled on the course's own "
             "practice material (lectures, readings, worked examples, review sheets, old exams; never open graded "
             "homework), and wait for my answer. If I'm right, say so, then raise the bar: ask why, or give a "
             "harder or less familiar variation. If I'm wrong, have me find my error before you explain it, and "
             "give the smallest hint that gets me moving. Keep a running score. Once I've shown I know it "
             "(several right, including a harder one), stop: give my score and anything to review, and offer more "
             "if I want it."),
    "review": ("Exam review", "Help me review for this. Find out what it covers (syllabus, review sheets, the "
               "course's pages in the library), then test me with a few quick questions across those topics "
               "before explaining anything, to see what I already know. From that, make a short study plan for "
               "the time left and work through it with me: support where I'm weak, harder problems where I'm not. "
               "When I've shown a topic, say so in a line and move on rather than asking more about it."),
    "canvas": ("Canvas", "We're working one problem together on a shared canvas (see \"The shared canvas\" in "
               "AGENTS.md): I write my work there and you add notes to it. The work is mine, so step in only when "
               "I press Help or ask, with the lightest help that gets me moving, and tell me plainly when I've "
               "got it right."),
}  # fmt: skip

# What the Canvas tab sends: the student set up a problem, pressed Help, or wrote in the chat beside it.
CANVAS_KINDS = {"setup": "Setup", "help": "Help", "chat": "Message"}
HELP_TEXT = "Help"  # a Help press with nothing typed, as the chat shows it


def chat_prompt(
    text: str, item: ItemOut | None, tz: ZoneInfo, first: bool, mode: str = "ask", course: str | None = None
) -> str:
    """The student's message. The first one in a conversation also says what the session is for."""
    if not first:
        return text
    label, instruction = MODES.get(mode, MODES["ask"])
    about = [f"course {course}"] if course else []
    if item is not None:
        about.append(f"about {item_line(item, tz, set())[2:]}")
    if instruction is None:
        return f"{text}\n\n(Asked from the dashboard; {'; '.join(about)}.)" if about else text
    context = f" ({'; '.join(about)})" if about else ""
    return f"[Tutoring session: {label}{context}]\n{instruction}\n\n{text}"


def canvas_prompt(text: str, canvas: dict, photos: list[str], first: bool, course: str | None = None) -> str:
    """A message from the Canvas tab, with the snapshot of the canvas it carries and how big a note is in it."""
    kind = canvas.get("kind", "chat")
    lines = []
    if first:
        label, instruction = MODES["canvas"]
        lines += [f"[Tutoring session: {label}{f' (course {course})' if course else ''}]", instruction, ""]
    said = "" if kind == "help" and text == HELP_TEXT else f" {text}"
    lines.append(f"[Canvas: {CANVAS_KINDS.get(kind, 'Message')}]{said}")
    return "\n".join(lines + _canvas_pictures(canvas, photos))


def session_canvas_prompt(
    text: str, canvas: dict, photos: list[str], item: ItemOut | None, tz: ZoneInfo, first: bool, mode: str,
    course: str | None = None,
) -> str:  # fmt: skip
    """A Tutor session's message sent with its canvas open beside the chat: the session's own prompt (its mode
    still holds), then the canvas."""
    help_press = canvas.get("kind") == "help" and text == HELP_TEXT
    prompt = chat_prompt("[Canvas: Help]" if help_press else text, item, tz, first=first, mode=mode, course=course)
    pictures = _canvas_pictures(canvas, photos) or ["[It's empty so far.]"]
    return f"{prompt}\n\n" + "\n".join(["[Sent with the session's canvas open beside the chat.]", *pictures])


def _canvas_pictures(canvas: dict, photos: list[str]) -> list[str]:
    """What a canvas message shows the agent: a photo of the problem, and the snapshot of the canvas."""
    lines = []
    snapshot = canvas.get("snapshot")
    problem = [p for p in photos if p != snapshot]
    if problem:
        paths = ", ".join(f'"uploads/{p}.jpg"' for p in problem)
        lines.append(f"[The student's photo of the problem, now on the canvas: await attach_image({paths})]")
    if snapshot:
        (w, h), (nw, nh) = canvas["size"], canvas["note"]
        lines.append(f"[Snapshot of the canvas, {w}×{h} px; a note takes about {nw}×{nh} px of it. Look before "
                     f'replying: await attach_image("uploads/{snapshot}.jpg")]')  # fmt: skip
    return lines


def with_photos(prompt: str, photos: list[str]) -> str:
    """Point the agent at photos the student attached, as paths in its workspace."""
    if not photos:
        return prompt
    paths = ", ".join(f'"uploads/{p}.jpg"' for p in photos)
    count = f"{len(photos)} photo{'s' if len(photos) > 1 else ''}"
    return f"{prompt}\n\n[The student attached {count}. Look before replying: await attach_image({paths})]"
