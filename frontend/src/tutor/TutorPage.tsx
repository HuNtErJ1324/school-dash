import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import type { Conversation, Course, Item, TutorMode } from "../api";
import { Composer, MessageList } from "../agent/Chat";
import { useAgent, useAsk, useConversation, useConversations, useDeleteConversation } from "../agent/hooks";
import { courseStyle } from "../courses";
import { hourFloor, useAllCourses, useCourses, useItems, useNow, useTimezone } from "../hooks";
import { Link, navigate } from "../router";
import { ago, DAY, shortDay } from "../time";
import { CanvasIcon, PlusIcon, TrashIcon } from "../ui/icons";
import { DEADLINE_KINDS, isDone } from "../widgets/parts";

// A session's canvas brings Excalidraw (a few MB), so it loads only when you open one.
const SessionCanvas = lazy(() => import("../canvas/SessionCanvas"));

const MODES: { mode: TutorMode; label: string; blurb: string; placeholder: string }[] = [
  {
    mode: "ask",
    label: "Ask",
    blurb: "A question about the course, answered from its materials.",
    placeholder: "Where does OSPP explain page tables? What's the difference between a lock and a semaphore?",
  },
  {
    mode: "explain",
    label: "Explain",
    blurb: "Learn a topic, starting from what you already know and building on it.",
    placeholder: "Separation of variables for the heat equation.",
  },
  {
    mode: "quiz",
    label: "Quiz me",
    blurb: "Practice questions one at a time. Get one right and the next gets harder.",
    placeholder: "Sequences and the ε–N definition of a limit.",
  },
  {
    mode: "review",
    label: "Exam review",
    blurb: "Tests what you know first, then a plan and practice for the time left.",
    placeholder: "Midterm 1 on Monday. I'm shakiest on proofs with sup and inf.",
  },
];
const MODE = Object.fromEntries(MODES.map((m) => [m.mode, m])) as Record<TutorMode, (typeof MODES)[number]>;
const modeLabel = (mode: Conversation["mode"]) => (mode === "canvas" ? "Canvas" : (MODE[mode]?.label ?? "Ask"));

/**
 * One-tap ways to tell the tutor where you are, so it knows whether to support you or push you
 * (the scaffold/rigor dial in the agent's AGENTS.md).
 */
const QUICK_REPLIES: { label: string; text: string }[] = [
  { label: "I'm stuck", text: "I'm stuck. Give me the smallest hint that gets me moving." },
  { label: "Harder one", text: "I've got this. Give me a harder one." },
  { label: "Let me try alone", text: "Let me try the next one on my own, then check my answer." },
  { label: "Where's this covered?", text: "Where is this covered in the course materials?" },
];

/**
 * Tutoring with the agent: a session per topic, kept so you can come back to it. A session can open a canvas
 * (/tutor/<id>/canvas), full screen with its chat beside it, to work problems by hand.
 */
export function TutorPage({ conversationId, canvas = false }: { conversationId: number | null; canvas?: boolean }) {
  useEffect(() => {
    document.title = "Tutor · School";
    return () => {
      document.title = "School";
    };
  }, []);
  return (
    <div className={conversationId === null ? "tutor" : "tutor in-session"}>
      <Sessions current={conversationId} />
      <section className="tutor-main" aria-label={conversationId === null ? "New session" : "Session"}>
        {conversationId === null ? <NewSession /> : <Session key={conversationId} id={conversationId} canvas={canvas} />}
      </section>
    </div>
  );
}

function courseById(courses: Course[] | undefined, id: string | null): Course | null {
  return (id && courses?.find((c) => c.id === id)) || null;
}

function Sessions({ current }: { current: number | null }) {
  const sessions = useConversations();
  const courses = useAllCourses();
  const inQuarter = useCourses();
  const now = useNow(60_000);
  const ids = new Set((inQuarter.data ?? []).map((c) => c.id));
  const list = (sessions.data ?? []).filter(
    (c) => c.mode !== "canvas" && (!c.course_id || ids.has(c.course_id) || c.id === current), // canvas sessions live on their canvases
  );
  return (
    <aside className="tutor-sessions" aria-label="Sessions">
      <Link to="/tutor" className={current === null ? "pill-button new-session" : "outline-button new-session"}>
        <PlusIcon /> New session
      </Link>
      {list.length > 0 && (
        <ul className="session-list">
          {list.map((c) => {
            const course = courseById(courses.data, c.course_id);
            return (
              <li key={c.id} style={courseStyle(course)}>
                <Link to={`/tutor/${c.id}`} aria-current={c.id === current ? "page" : undefined}>
                  <span className="session-title">{c.title}</span>
                  <span className="session-sub">
                    {course && <span className="course-code">{course.code}</span>}
                    <span>{modeLabel(c.mode)}</span>
                    {c.canvas_id !== null && <span>Canvas</span>}
                    <span>{ago(now - Date.parse(c.updated_at))}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {sessions.isSuccess && list.length === 0 && <p className="quiet">Your sessions will be listed here.</p>}
    </aside>
  );
}

/** Upcoming work a session can be about: open assignments and exams in the next few weeks. */
function useUpcoming(courseId: string | null): Item[] {
  const hour = hourFloor(useNow());
  const query = useItems({ start: new Date(hour - DAY), end: new Date(hour + 28 * DAY), kinds: DEADLINE_KINDS });
  return useMemo(
    () =>
      (query.data ?? [])
        .filter((i) => !isDone(i) && (!courseId || i.course?.id === courseId))
        .sort((a, b) => Number(b.kind === "exam") - Number(a.kind === "exam") || Date.parse(a.at!) - Date.parse(b.at!))
        .slice(0, 6),
    [query.data, courseId],
  );
}

function NewSession() {
  const agent = useAgent();
  const courses = useCourses();
  const tz = useTimezone();
  const ask = useAsk();
  const [courseId, setCourseId] = useState<string | null>(null);
  const [mode, setMode] = useState<TutorMode>("explain");
  const [itemId, setItemId] = useState<number | null>(null);
  const upcoming = useUpcoming(courseId);
  const focus = upcoming.find((i) => i.id === itemId) ?? null;

  const start = (text: string, photos: string[], done: () => void) =>
    ask.mutate(
      { id: null, text, photos, start: { courseId: courseId ?? focus?.course?.id ?? null, mode, itemId: focus?.id ?? null } },
      {
        onSuccess: (c) => {
          done();
          navigate(`/tutor/${c.id}`);
        },
      },
    );

  return (
    <div className="new-session-panel">
      <h2 className="tutor-heading">What do you want to work on?</h2>
      <fieldset className="tutor-field">
        <legend className="field-label">Course</legend>
        <div className="chips">
          <label className="chip">
            <input type="radio" name="course" checked={courseId === null} onChange={() => setCourseId(null)} />
            Any course
          </label>
          {(courses.data ?? []).map((c) => (
            <label key={c.id} className="chip" style={courseStyle(c)}>
              <input
                type="radio"
                name="course"
                checked={courseId === c.id}
                onChange={() => {
                  setCourseId(c.id);
                  setItemId(null);
                }}
              />
              {c.code}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="tutor-field">
        <legend className="field-label">How</legend>
        <div className="modes">
          {MODES.map((m) => (
            <label key={m.mode} className="mode-card">
              <input type="radio" name="mode" checked={mode === m.mode} onChange={() => setMode(m.mode)} />
              <span className="mode-label">{m.label}</span>
              <span className="mode-blurb">{m.blurb}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {upcoming.length > 0 && (
        <fieldset className="tutor-field">
          <legend className="field-label">About (optional)</legend>
          <div className="chips">
            {upcoming.map((i) => (
              <label key={i.id} className="chip" style={courseStyle(i.course)}>
                <input type="checkbox" checked={itemId === i.id} onChange={() => setItemId(itemId === i.id ? null : i.id)} />
                {!courseId && i.course && <span className="course-code">{i.course.code}</span>} {i.title}
                <span className="chip-when">{shortDay(new Date(i.at!), tz)}</span>
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {agent.data && !agent.data.ready ? (
        <p className="quiet">{agent.data.problem ?? "The agent isn't set up."}</p>
      ) : (
        <Composer
          rows={3}
          label="What to work on"
          placeholder={MODE[mode].placeholder}
          busy={ask.isPending}
          onSend={start}
          autoFocus={window.matchMedia("(min-width: 761px)").matches} // not on phones: it would open the keyboard
          photos
        />
      )}
      {ask.isError && <p className="problem">{ask.error.message}</p>}
      <p className="field-help tutor-note">
        It teaches, quizzes and points you to your course materials. It won't do graded homework, labs or psets for you,
        or check your answers to them.
      </p>
    </div>
  );
}

function Session({ id, canvas }: { id: number; canvas: boolean }) {
  const conv = useConversation(id);
  const courses = useAllCourses();
  const remove = useDeleteConversation();
  const end = useRef<HTMLDivElement>(null);
  const count = conv.data?.messages.length ?? 0;
  const seen = useRef(0);

  // Open at the latest message, then follow new ones as they arrive (and come back to it from the canvas).
  useEffect(() => {
    if (!canvas && count > seen.current) end.current?.scrollIntoView({ block: seen.current === 0 ? "end" : "nearest" });
    seen.current = canvas ? 0 : count;
  }, [count, canvas]);

  if (conv.isPending) return <p className="quiet">Loading…</p>;
  if (conv.isError) {
    return (
      <p className="problem">
        This session couldn't be loaded. <Link to="/tutor">Start a new one</Link>.
      </p>
    );
  }
  const c: Conversation = conv.data;
  const course = courseById(courses.data, c.course_id);
  const spent = c.messages.reduce((sum, m) => sum + (m.cost ?? 0), 0);

  return (
    <article className="session" style={courseStyle(course)}>
      <header className="session-head">
        <Link to="/tutor" className="text-button session-back">
          All sessions
        </Link>
        <h2 className="tutor-heading">{c.title}</h2>
        <p className="session-meta">
          {course && <span className="course-code">{course.code}</span>}
          <span>{modeLabel(c.mode)}</span>
          {spent > 0 && <span>${spent.toFixed(2)} so far</span>}
          <button
            type="button"
            className="icon-button"
            aria-label="Delete this session"
            onClick={() => {
              const what = c.canvas_id === null ? "this session" : "this session and its canvas";
              if (confirm(`Delete ${what}?`)) remove.mutate(id, { onSuccess: () => navigate("/tutor", { replace: true }) });
            }}
          >
            <TrashIcon />
          </button>
        </p>
      </header>
      {canvas ? (
        <Suspense fallback={<p className="quiet">Opening the canvas…</p>}>
          <SessionCanvas
            conversationId={id}
            title={c.title}
            quickReplies={c.mode === "ask" ? [] : QUICK_REPLIES}
            placeholder={c.mode === "quiz" ? "Your answer…" : "Reply or ask a follow-up…"}
            onClose={() => navigate(`/tutor/${id}`)}
          />
        </Suspense>
      ) : (
        <SessionChat conversation={c} />
      )}
      {/* Below the reply box, so scrolling to it shows the whole last message above the pinned box. */}
      <div ref={end} />
    </article>
  );
}

/** A session's chat, when its canvas isn't open. */
function SessionChat({ conversation: c }: { conversation: Conversation }) {
  const agent = useAgent();
  const ask = useAsk();
  const id = c.id;
  const answering = c.messages.at(-1)?.status === "running";
  return (
    <>
      <MessageList messages={c.messages} />
      <div className="session-composer">
        {agent.data && !agent.data.ready ? (
          <p className="quiet">{agent.data.problem ?? "The agent isn't set up."}</p>
        ) : (
          <>
            {c.mode !== "ask" && !answering && (
              <div className="quick-replies" aria-label="Quick replies">
                {QUICK_REPLIES.map((q) => (
                  <button
                    key={q.label}
                    type="button"
                    className="chip"
                    disabled={ask.isPending}
                    onClick={() => ask.mutate({ id, text: q.text })}
                  >
                    {q.label}
                  </button>
                ))}
              </div>
            )}
            <Composer
              placeholder={c.mode === "quiz" ? "Your answer…" : "Reply or ask a follow-up…"}
              label={c.mode === "quiz" ? "Your answer" : "Your reply"}
              busy={answering || ask.isPending}
              onSend={(text, photos, done) => ask.mutate({ id, text, photos }, { onSuccess: done })}
              photos
              tools={
                <button
                  type="button"
                  className="icon-button composer-photo"
                  aria-label="Open the canvas"
                  title="Work it by hand on a canvas: the tutor sees your work and writes notes on it"
                  onClick={() => navigate(`/tutor/${id}/canvas`)}
                >
                  <CanvasIcon />
                </button>
              }
            />
          </>
        )}
        {ask.isError && <p className="problem">{ask.error.message}</p>}
      </div>
    </>
  );
}
