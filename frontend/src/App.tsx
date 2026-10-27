import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useState } from "react";

import { Board } from "./board/Board";
import { Header, type Page } from "./Header";
import { QuarterProvider, useQuarter } from "./quarter";
import { usePath } from "./router";
import { TutorPage } from "./tutor/TutorPage";

// The canvas brings Excalidraw (a few MB), so it loads only when you open the tab.
const CanvasPage = lazy(() => import("./canvas/CanvasPage"));

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

export function App() {
  const [editing, setEditing] = useState(false);
  const path = usePath();
  const tutor = /^\/tutor(?:\/(\d+)(\/canvas)?)?\/?$/.exec(path);
  const canvas = /^\/canvas(?:\/(\d+))?\/?$/.exec(path);
  const page: Page = tutor ? "tutor" : canvas ? "canvas" : "board";
  return (
    <QueryClientProvider client={client}>
      <QuarterProvider>
        <Header page={page} editing={editing} onToggleEditing={() => setEditing(!editing)} />
        <QuarterNotice />
        {tutor ? (
          <TutorPage conversationId={tutor[1] ? Number(tutor[1]) : null} canvas={Boolean(tutor[1] && tutor[2])} />
        ) : canvas ? (
          <Suspense fallback={<p className="quiet">Loading the canvas…</p>}>
            <CanvasPage canvasId={canvas[1] ? Number(canvas[1]) : null} />
          </Suspense>
        ) : (
          <Board editing={editing} />
        )}
      </QuarterProvider>
    </QueryClientProvider>
  );
}

/** Says so when you're looking at a quarter other than the current one, with a way back. */
function QuarterNotice() {
  const { selected, current, quarters, isCurrent, select } = useQuarter();
  if (isCurrent || !selected || !current) return null;
  const index = (code: string) => quarters.findIndex((q) => q.code === code); // newest first
  const name = (code: string) => quarters[index(code)]?.name ?? code;
  return (
    <p className="quarter-notice" role="status">
      <span>
        You're looking at <b>{name(selected)}</b>.{" "}
        {index(selected) > index(current)
          ? "Its deadlines are over, so the schedule widgets and today's plan are about now. Grades, courses and the library show how it went."
          : "It hasn't started yet; its courses and deadlines appear as Canvas and Gradescope publish them."}
      </span>
      <button type="button" className="text-button" onClick={() => select(current)}>
        Back to {name(current)}
      </button>
    </p>
  );
}
