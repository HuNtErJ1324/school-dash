import "./assets";
import "@excalidraw/excalidraw/index.css";

import { CaptureUpdateAction, Excalidraw, getSceneVersion, getVisibleSceneBounds, MainMenu, WelcomeScreen } from "@excalidraw/excalidraw";
import type { ExcalidrawElement, OrderedExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { AppState, BinaryFiles, ExcalidrawImperativeAPI, ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

import { api, canvasFileUrl, type CanvasData, type CanvasSnapshot, type Message } from "../api";
import { Composer, MessageList } from "../agent/Chat";
import { useAgent, useConversation } from "../agent/hooks";
import { Markdown } from "../agent/Markdown";
import { courseStyle } from "../courses";
import { useAllCourses } from "../hooks";
import { Link, navigate } from "../router";
import { CloseIcon, TrashIcon } from "../ui/icons";
import {
  type Action,
  blobToDataUrl,
  dataUrlToBlob,
  draw,
  type Frame,
  IDENTITY,
  imageFile,
  NOTE,
  parseReply,
  snapshot,
} from "./annotate";
import { ChatResizer, useChatSize } from "./ChatResizer";
import { useCanvasAsk, useDeleteCanvas, useRenameCanvas } from "./hooks";

/** The page's bookkeeping, saved with the drawing. */
interface PageState {
  applied: number[]; // replies already drawn, so a reload doesn't draw them twice
  frames: Record<string, Frame>; // where each snapshot sat on the canvas, by its upload id
  problem?: string; // the statement the tutor found, in markdown
}

function readState(raw: Record<string, unknown>): PageState {
  const applied = Array.isArray(raw.applied) ? raw.applied.filter((x): x is number => typeof x === "number") : [];
  const frames = raw.frames && typeof raw.frames === "object" ? (raw.frames as Record<string, Frame>) : {};
  return { applied, frames, ...(typeof raw.problem === "string" && { problem: raw.problem }) };
}

const QUICK_REPLIES: { label: string; text: string }[] = [
  { label: "Check my work", text: "Check my work so far." },
  { label: "Smaller hint", text: "That's still too much. Give me a smaller hint." },
  { label: "Where's this covered?", text: "Where is this covered in the course materials?" },
];

/** How a Tutor session shows its canvas: full screen, with the session's chat beside it and a way back. */
export interface SessionOptions {
  title: string;
  quickReplies: { label: string; text: string }[];
  placeholder: string;
  onClose: () => void;
}

/**
 * A canvas, once its images are in: Excalidraw needs them as data URLs before it draws the scene. On its own
 * (the Canvas tab) it has a header to rename or delete it; in a Tutor session, the session's header does that.
 */
export function CanvasBoard({ canvas, session }: { canvas: CanvasData; session?: SessionOptions }) {
  const [initial, setInitial] = useState<ExcalidrawInitialDataState | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadInitial(canvas).then(
      (data) => live && setInitial(data),
      (e: Error) => live && setFailed(e.message),
    );
    return () => {
      live = false;
    };
  }, [canvas]);
  if (failed) return <p className="problem canvas-message">This canvas couldn't be loaded: {failed}</p>;
  if (!initial) return <p className="quiet canvas-message">Loading…</p>;
  return <Board canvas={canvas} initial={initial} session={session} />;
}

// Excalidraw's dark mode inverts the drawing (invert(93%) hue-rotate(180deg)), so these are the colors that come
// out as the site's page (--bg) and text (--text). Snapshots for the tutor are exported light: white, dark ink.
const PAGE = "#f5f4ff";
const PEN = "#0f0d26";

async function loadInitial(canvas: CanvasData): Promise<ExcalidrawInitialDataState> {
  const files: BinaryFiles = {};
  await Promise.all(
    canvas.files.map(async (f) => {
      const res = await fetch(canvasFileUrl(canvas.id, f.id));
      if (res.ok) files[f.id] = imageFile(f.id, await blobToDataUrl(await res.blob()), f.mime_type);
    }),
  );
  const saved = canvas.scene.appState ?? {};
  const view = Object.fromEntries(Object.entries(saved).filter(([k]) => ["scrollX", "scrollY", "zoom"].includes(k)));
  return {
    elements: (canvas.scene.elements ?? []) as ExcalidrawElement[],
    files,
    appState: { ...view, viewBackgroundColor: PAGE, currentItemStrokeColor: PEN, currentItemStrokeWidth: 1 },
    scrollToContent: !("scrollX" in saved),
  };
}

function Board({ canvas, initial, session }: { canvas: CanvasData; initial: ExcalidrawInitialDataState; session?: SessionOptions }) {
  const excalidraw = useRef<ExcalidrawImperativeAPI | null>(null);
  const page = useRef(readState(canvas.state));
  const saver = useAutosave(canvas, page);
  const [ready, setReady] = useState(false); // the scene is loaded
  const [convId, setConvId] = useState(canvas.conversation_id);
  const conv = useConversation(convId);
  const ask = useCanvasAsk(canvas.id);
  const agent = useAgent();
  const courses = useAllCourses();
  const { mutate: rename } = useRenameCanvas();
  const remove = useDeleteCanvas();
  const [title, setTitle] = useState(canvas.title);
  // A session's chat starts open beside its canvas, unless the screen is too narrow for both.
  const [chatOpen, setChatOpen] = useState(() => Boolean(session) && matchMedia("(min-width: 761px)").matches);
  const [unread, setUnread] = useState(0);
  const chatSize = useChatSize();
  const [sending, setSending] = useState(false);
  const chatOpenNow = useRef(chatOpen);
  chatOpenNow.current = chatOpen;

  const messages = conv.data?.messages ?? [];
  const answering = messages.at(-1)?.status === "running";
  const busy = sending || answering || ask.isPending;
  const agentReady = !agent.data || agent.data.ready;
  const course = courses.data?.find((c) => c.id === canvas.course_id) ?? null;
  const spent = messages.reduce((sum, m) => sum + (m.cost ?? 0), 0);

  const retitle = useCallback(
    (next: string) => {
      const clean = next.trim().slice(0, 200);
      if (!clean) return;
      setTitle(clean);
      rename({ id: canvas.id, title: clean });
    },
    [canvas.id, rename],
  );
  const { touch } = saver;
  const [problem, setProblem] = useState(page.current.problem ?? null);
  const showProblem = useCallback(
    (markdown: string) => {
      page.current.problem = markdown;
      setProblem(markdown);
      touch();
    },
    [touch],
  );

  // Draw each reply's additions once, as it arrives (or on the next visit, if it came in while you were away).
  useEffect(() => {
    const ex = excalidraw.current;
    if (!ready || !ex || !conv.data) return;
    const all = conv.data.messages;
    all.forEach((m, i) => {
      if (m.role !== "assistant" || m.status === "running" || page.current.applied.includes(m.id)) return;
      page.current.applied.push(m.id);
      touch();
      const { actions } = m.status === "ready" ? parseReply(m.text) : { actions: [] as Action[] };
      const asked = all.slice(0, i).findLast((u) => u.role === "user");
      const frame = (asked?.canvas?.snapshot && page.current.frames[asked.canvas.snapshot]) || IDENTITY;
      try {
        addToCanvas(ex, actions, frame, m.id, { title: retitle, problem: showProblem });
      } catch (e) {
        ex.setToast({ message: `Couldn't draw the tutor's reply: ${(e as Error).message}`, closable: true });
      }
      // Notes speak for themselves; a reply that's only words (or an error) opens the chat.
      if (!chatOpenNow.current) {
        if (actions.some((a) => !("title" in a))) setUnread((n) => n + 1);
        else setChatOpen(true);
      }
    });
  }, [conv.data, ready, touch, retitle, showProblem]);

  const send = async (kind: "help" | "chat", text = ""): Promise<boolean> => {
    const ex = excalidraw.current;
    if (!ex || busy) return false;
    setSending(true);
    try {
      const shot = await snapshot(ex);
      let snap: CanvasSnapshot | undefined;
      if (shot) {
        const photo = await api.uploadPhoto(new File([shot.blob], "canvas.png", { type: "image/png" }));
        const frame = { ...shot.frame, scale: (shot.frame.scale * photo.width) / shot.width }; // in case the server shrank it
        page.current.frames = keepLast({ ...page.current.frames, [photo.id]: frame }, 40);
        saver.touch();
        const note: [number, number] = [Math.round(NOTE.width * frame.scale), Math.round(NOTE.height * frame.scale)];
        snap = { id: photo.id, width: photo.width, height: photo.height, note };
      }
      const c = await ask.mutateAsync({ kind, text, snapshot: snap });
      setConvId(c.id);
      saver.flush();
      return true;
    } catch (e) {
      ex.setToast({ message: `Couldn't ask the tutor: ${(e as Error).message}`, closable: true, duration: 8000 });
      return false;
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="canvas-board" style={courseStyle(course)}>
      {!session && (
        <header className="canvas-head">
          <Link to="/canvas" className="text-button canvas-back">
            All canvases
          </Link>
          <input
            className="canvas-title"
            value={title}
            maxLength={200}
            aria-label="Name of this canvas"
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => (title.trim() && title.trim() !== canvas.title ? retitle(title) : setTitle(title.trim() || canvas.title))}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          />
          <p className="session-meta">
            {course && <span className="course-code">{course.code}</span>}
            {spent > 0 && <span>${spent.toFixed(2)} so far</span>}
            {saver.error && <span className="problem">Not saved: {saver.error}</span>}
            <button
              type="button"
              className="icon-button"
              aria-label="Delete this canvas"
              onClick={() => {
                if (confirm("Delete this canvas and its conversation with the tutor?")) {
                  saver.cancel();
                  remove.mutate(canvas.id, { onSuccess: () => navigate("/canvas", { replace: true }) });
                }
              }}
            >
              <TrashIcon />
            </button>
          </p>
        </header>
      )}
      {session && saver.error && <p className="problem canvas-save-problem">Not saved: {saver.error}</p>}
      <div className={chatOpen ? "canvas-body chat-open" : "canvas-body"} style={chatSize.style}>
        <div className="canvas-stage">
          {problem && <ProblemCard markdown={problem} />}
          <Excalidraw
            excalidrawAPI={(ex) => (excalidraw.current = ex)}
            initialData={initial}
            onChange={(elements, appState, files) => {
              if (!ready) {
                setReady(true);
                excalidraw.current?.setActiveTool({ type: "freedraw" }); // it's for writing
              }
              saver.record(elements, appState, files);
            }}
            theme="dark"
            name={title}
            aiEnabled={false}
            UIOptions={{
              canvasActions: { loadScene: false, saveToActiveFile: false, export: false, toggleTheme: false },
              tools: { image: true },
            }}
            renderTopRightUI={() => (
              <div className="canvas-actions">
                <button
                  type="button"
                  className="pill-button canvas-help"
                  aria-label={busy ? "The tutor is thinking" : "Help from the tutor"} // Excalidraw has its own Help (shortcuts)
                  disabled={busy || !agentReady}
                  title={agentReady ? "The tutor looks at the whole canvas and adds a hint" : (agent.data?.problem ?? undefined)}
                  onClick={() => void send("help")}
                >
                  {busy ? "Thinking…" : "Help"}
                </button>
                <button
                  type="button"
                  className="outline-button canvas-chat-toggle"
                  aria-pressed={chatOpen}
                  onClick={() => {
                    setChatOpen(!chatOpen);
                    setUnread(0);
                  }}
                >
                  Chat{unread > 0 && <span className="canvas-unread">{unread}</span>}
                </button>
                {session && (
                  <button
                    type="button"
                    className="outline-button canvas-close"
                    aria-label="Close the canvas"
                    title="Back to the chat (your work is saved)"
                    onClick={session.onClose}
                  >
                    <CloseIcon />
                  </button>
                )}
              </div>
            )}
          >
            <MainMenu>
              <MainMenu.DefaultItems.SaveAsImage />
              <MainMenu.DefaultItems.ClearCanvas />
              <MainMenu.Separator />
              <MainMenu.DefaultItems.Help />
            </MainMenu>
            <WelcomeScreen>
              <WelcomeScreen.Hints.ToolbarHint />
              <WelcomeScreen.Center>
                <WelcomeScreen.Center.Heading>Work the problem here. Press Help when you want a nudge.</WelcomeScreen.Center.Heading>
              </WelcomeScreen.Center>
            </WelcomeScreen>
          </Excalidraw>
        </div>
        {chatOpen && (
          <aside className="canvas-chat" aria-label="Chat with the tutor">
            <ChatResizer size={chatSize.size} setSize={chatSize.setSize} />
            <header className="canvas-chat-head">
              <h3>{session?.title ?? "Tutor"}</h3>
              <button type="button" className="icon-button" aria-label="Close the chat" onClick={() => setChatOpen(false)}>
                <CloseIcon />
              </button>
            </header>
            <ChatMessages messages={messages} />
            <div className="canvas-chat-composer">
              {!agentReady ? (
                <p className="quiet">{agent.data?.problem ?? "The agent isn't set up."}</p>
              ) : (
                <>
                  {!busy && messages.length > 0 && (
                    <div className="quick-replies" aria-label="Quick replies">
                      {(session?.quickReplies ?? QUICK_REPLIES).map((q) => (
                        <button key={q.label} type="button" className="chip" onClick={() => void send("chat", q.text)}>
                          {q.label}
                        </button>
                      ))}
                    </div>
                  )}
                  <Composer
                    placeholder={session?.placeholder ?? "Ask about your work…"}
                    label="Your message"
                    busy={busy}
                    onSend={(text, _photos, done) => void send("chat", text).then((ok) => ok && done())}
                  />
                </>
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

function ChatMessages({ messages }: { messages: Message[] }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" }); // braces: browsers now return a promise from it
  }, [messages.length, messages.at(-1)?.status]);
  return (
    <div className="canvas-chat-messages">
      {messages.length ? (
        <MessageList messages={messages} />
      ) : (
        <p className="quiet">Press Help when you're stuck, or ask here. The tutor sees the whole canvas each time.</p>
      )}
      <div ref={end} />
    </div>
  );
}

/** The problem statement, pinned over the canvas so it stays in view while you work. Collapses out of the way. */
function ProblemCard({ markdown }: { markdown: string }) {
  const [open, setOpen] = useState(true);
  return (
    <section className={open ? "problem-pin" : "problem-pin collapsed"} aria-label="The problem">
      <button type="button" className="problem-pin-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        Problem
      </button>
      {open && (
        <div className="problem-pin-body">
          <Markdown text={markdown} />
        </div>
      )}
    </section>
  );
}

/** Puts a reply's additions on the canvas, and brings them into view if they landed off-screen. */
function addToCanvas(
  ex: ExcalidrawImperativeAPI,
  actions: Action[],
  frame: Frame,
  messageId: number,
  on: { title: (title: string) => void; problem: (markdown: string) => void },
) {
  for (const a of actions) {
    if ("title" in a) on.title(a.title);
    if ("problem" in a) on.problem(a.problem);
  }
  const added = draw(actions, frame, messageId);
  if (!added.length) return;
  ex.updateScene({ elements: [...ex.getSceneElementsIncludingDeleted(), ...added], captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  const [x1, y1, x2, y2] = getVisibleSceneBounds(ex.getAppState());
  if (!added.every((e) => e.x >= x1 && e.y >= y1 && e.x + e.width <= x2 && e.y + e.height <= y2)) {
    ex.scrollToContent(added, { animate: true });
  }
}

/** The scene as Excalidraw last reported it, loaded and alive. */
interface Reported {
  elements: readonly OrderedExcalidrawElement[]; // deleted ones included
  appState: AppState;
  files: BinaryFiles;
}

/**
 * Saves the drawing a moment after you stop, and right away when you leave the page. It saves what Excalidraw
 * last reported through onChange, never what it says while unmounting: by then its scene is empty, and saving
 * that would wipe the canvas. Images go up once each, by their Excalidraw file id.
 */
function useAutosave(canvas: CanvasData, page: RefObject<PageState>) {
  const timer = useRef<number | undefined>(undefined);
  const reported = useRef<Reported | null>(null);
  const listening = useRef(true); // off from the moment the board starts to unmount
  const cancelled = useRef(false); // the canvas was deleted
  const last = useRef("");
  const stateVersion = useRef(0);
  const savedFiles = useRef(new Set(canvas.files.map((f) => f.id)));
  const [error, setError] = useState<string | null>(null);

  const flush = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    const now = reported.current;
    if (!now || cancelled.current) return;
    for (const [id, file] of Object.entries(now.files)) {
      if (savedFiles.current.has(id)) continue;
      savedFiles.current.add(id);
      dataUrlToBlob(file.dataURL)
        .then((blob) => api.saveCanvasFile(canvas.id, id, blob))
        .catch((e: Error) => {
          savedFiles.current.delete(id);
          setError(e.message);
        });
    }
    const { scrollX, scrollY, zoom, viewBackgroundColor } = now.appState;
    const signature = [getSceneVersion(now.elements), Math.round(scrollX), Math.round(scrollY), zoom.value, stateVersion.current].join(":");
    if (signature === last.current) return;
    last.current = signature;
    const elements = now.elements.filter((e) => !e.isDeleted);
    api
      .saveCanvasScene(canvas.id, { elements, appState: { scrollX, scrollY, zoom, viewBackgroundColor }, state: { ...page.current } })
      .then(
        () => setError(null),
        (e: Error) => {
          last.current = ""; // try again next time
          setError(e.message);
        },
      );
  }, [canvas.id, page]);

  const schedule = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, 800);
  }, [flush]);
  /** Excalidraw's onChange. It only reports once the saved scene is in (not while isLoading). */
  const record = useCallback(
    (elements: readonly OrderedExcalidrawElement[], appState: AppState, files: BinaryFiles) => {
      if (!listening.current) return;
      reported.current = { elements, appState, files };
      schedule();
    },
    [schedule],
  );
  const touch = useCallback(() => {
    stateVersion.current += 1;
    schedule();
  }, [schedule]);
  const cancel = useCallback(() => {
    cancelled.current = true;
    window.clearTimeout(timer.current);
  }, []);

  // Layout cleanups run parent first, so this stops listening before Excalidraw unmounts.
  useLayoutEffect(() => {
    listening.current = true;
    return () => {
      listening.current = false;
    };
  }, []);

  useEffect(() => {
    const hidden = () => document.visibilityState === "hidden" && flush();
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [flush]);

  return { record, touch, flush, cancel, error };
}

function keepLast<T>(record: Record<string, T>, n: number): Record<string, T> {
  return Object.fromEntries(Object.entries(record).slice(-n));
}
