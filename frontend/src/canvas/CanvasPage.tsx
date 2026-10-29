import { convertToExcalidrawElements } from "@excalidraw/excalidraw";
import type { ExcalidrawElement, FileId } from "@excalidraw/excalidraw/element/types";
import { useEffect, useState } from "react";

import { api, photoUrl } from "../api";
import { Composer } from "../agent/Chat";
import { useAgent } from "../agent/hooks";
import { courseStyle } from "../courses";
import { useAllCourses, useCourses, useNow } from "../hooks";
import { Link, navigate } from "../router";
import { ago } from "../time";
import { PlusIcon } from "../ui/icons";
import { CanvasBoard } from "./CanvasBoard";
import { useCanvas, useCanvases, useCreateCanvas } from "./hooks";
import { useFillHeight } from "./useFillHeight";

/**
 * The Canvas tab: work a problem by hand on a whiteboard the tutor can see. You write; Help (or the chat)
 * sends it a snapshot, and it answers with notes on the canvas next to your work.
 */
export default function CanvasPage({ canvasId }: { canvasId: number | null }) {
  useEffect(() => {
    document.title = "Canvas · School";
    return () => {
      document.title = "School";
    };
  }, []);
  const fill = useFillHeight();
  return (
    <div
      ref={fill.ref}
      className={canvasId === null ? "canvas-page" : "canvas-page open"}
      style={{ height: canvasId === null ? undefined : fill.height }}
    >
      <CanvasList current={canvasId} />
      <section className="canvas-main" aria-label={canvasId === null ? "New problem" : "Canvas"}>
        {canvasId === null ? <NewCanvas /> : <OpenCanvas key={canvasId} id={canvasId} />}
      </section>
    </div>
  );
}

function CanvasList({ current }: { current: number | null }) {
  const canvases = useCanvases();
  const courses = useAllCourses();
  const inQuarter = useCourses();
  const now = useNow(60_000);
  const ids = new Set((inQuarter.data ?? []).map((c) => c.id));
  const list = (canvases.data ?? []).filter((c) => !c.course_id || ids.has(c.course_id) || c.id === current);
  return (
    <aside className="tutor-sessions canvas-list" aria-label="Canvases">
      <Link to="/canvas" className={current === null ? "pill-button new-session" : "outline-button new-session"}>
        <PlusIcon /> New problem
      </Link>
      {list.length > 0 && (
        <ul className="session-list">
          {list.map((c) => {
            const course = courses.data?.find((x) => x.id === c.course_id) ?? null;
            return (
              <li key={c.id} style={courseStyle(course)}>
                <Link to={`/canvas/${c.id}`} aria-current={c.id === current ? "page" : undefined}>
                  <span className="session-title">{c.title}</span>
                  <span className="session-sub">
                    {course && <span className="course-code">{course.code}</span>}
                    <span>{ago(now - Date.parse(c.updated_at))}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {canvases.isSuccess && list.length === 0 && <p className="quiet">Your canvases will be listed here.</p>}
    </aside>
  );
}

function OpenCanvas({ id }: { id: number }) {
  const canvas = useCanvas(id);
  if (canvas.isPending) return <p className="quiet canvas-message">Loading…</p>;
  if (canvas.isError) {
    return (
      <p className="problem canvas-message">
        This canvas couldn't be loaded. <Link to="/canvas">Start a new one</Link>.
      </p>
    );
  }
  return <CanvasBoard canvas={canvas.data} />;
}

const PHOTO_WIDTH = 600; // canvas units: a photo of the problem, about a page wide

function NewCanvas() {
  const agent = useAgent();
  const courses = useCourses();
  const create = useCreateCanvas();
  const [courseId, setCourseId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const agentReady = !agent.data || agent.data.ready;

  const start = async (text: string, photos: string[], done?: () => void) => {
    setBusy(true);
    setProblem(null);
    try {
      const title = text ? oneLine(text, 80) : photos.length ? "Problem from a photo" : "Untitled problem";
      const canvas = await create.mutateAsync({ title, course_id: courseId });
      if (photos.length) await putPhotos(canvas.id, photos);
      // A typed or named problem goes to the tutor, which finds it and puts it on the canvas. A photo is
      // already there, so nothing's spent until you ask.
      if (text) await api.askCanvas(canvas.id, { kind: "setup", text, photos });
      done?.();
      navigate(`/canvas/${canvas.id}`);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="new-session-panel canvas-new">
      <h2 className="tutor-heading">What problem are you working on?</h2>
      <fieldset className="tutor-field">
        <legend className="field-label">Course</legend>
        <div className="chips">
          <label className="chip">
            <input type="radio" name="course" checked={courseId === null} onChange={() => setCourseId(null)} />
            Any course
          </label>
          {(courses.data ?? []).map((c) => (
            <label key={c.id} className="chip" style={courseStyle(c)}>
              <input type="radio" name="course" checked={courseId === c.id} onChange={() => setCourseId(c.id)} />
              {c.code}
            </label>
          ))}
        </div>
      </fieldset>
      <p className="field-help">
        Name it (“HW 3 problem 2”, “§7.5 #14”) and the tutor finds it in your course materials, or paste it, or attach a
        photo of it. Then work it on the canvas and press Help when you want a nudge.
      </p>
      {agentReady ? (
        <Composer
          rows={3}
          label="The problem"
          placeholder="HW 3 problem 2, or paste the problem"
          busy={busy}
          onSend={(text, photos, done) => void start(text, photos, done)}
          autoFocus={window.matchMedia("(min-width: 761px)").matches}
          photos
          photoOnlyText=""
          submitLabel="Start"
        />
      ) : (
        <>
          <p className="quiet">{agent.data?.problem ?? "The agent isn't set up."} You can still draw on a canvas.</p>
          <Composer rows={2} label="The problem" placeholder="Attach a photo of the problem" busy={busy} onSend={(_t, photos, done) => void start("", photos, done)} photos photoOnlyText="" submitLabel="Start" />
        </>
      )}
      <button type="button" className="text-button canvas-blank" disabled={busy} onClick={() => void start("", [])}>
        Or start with a blank canvas
      </button>
      {problem && <p className="problem">{problem}</p>}
      <p className="field-help tutor-note">
        It gives hints and questions, not answers. On graded homework still open, it explains the ideas and tells you
        whether your answer is right, but won't check your steps.
      </p>
    </div>
  );
}

function oneLine(text: string, max: number): string {
  const line = text.split("\n").find((l) => l.trim())?.trim() ?? text.trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** Puts photos of the problem on a new canvas, top to bottom, before it opens. */
async function putPhotos(canvasId: number, photos: string[]) {
  const elements: ExcalidrawElement[] = [];
  let y = 0;
  for (const id of photos) {
    const blob = await (await fetch(photoUrl(id))).blob();
    const fileId = `photo-${id}`;
    await api.saveCanvasFile(canvasId, fileId, blob);
    const bitmap = await createImageBitmap(blob);
    const height = (bitmap.height / bitmap.width) * PHOTO_WIDTH;
    bitmap.close();
    elements.push(
      ...convertToExcalidrawElements([{ type: "image", x: 0, y, width: PHOTO_WIDTH, height, fileId: fileId as FileId, status: "saved" }]),
    );
    y += height + 24;
  }
  await api.saveCanvasScene(canvasId, { elements, appState: {}, state: {} });
}
