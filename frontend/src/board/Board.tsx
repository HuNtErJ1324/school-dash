import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import { useCallback, useMemo, useRef, useState } from "react";
import { Responsive, useContainerWidth, type ResponsiveLayouts } from "react-grid-layout";

import { PlusIcon } from "../ui/icons";
import { Popover } from "../ui/Popover";
import { definition, widgets, type WidgetType } from "../widgets/registry";
import {
  addWidget, BREAKPOINTS, COLS, defaultBoard, removeWidget, replaceWidget, stackOrder, STACK_BELOW,
  useBoard, withLayouts, type BoardState, type SaveState,
} from "./boardState"; // fmt: skip
import { WidgetFrame } from "./WidgetFrame";
import { WidgetSettings } from "./WidgetSettings";

const ROW_HEIGHT = 40;
const GAP = 20;

export function Board({ editing }: { editing: boolean }) {
  const { board, update, saveState } = useBoard();
  const { width, containerRef, mounted } = useContainerWidth();
  const [settingsFor, setSettingsFor] = useState<string | null>(null);

  // The grid needs each widget's minimum size; the saved board doesn't store it.
  const layouts = useMemo(() => (board ? withMinimums(board) : undefined), [board]);
  const onLayoutChange = useCallback(
    (_: unknown, all: ResponsiveLayouts) => {
      if (editing) update((b) => withLayouts(b, all)); // the grid also reports on mount; only edits count
    },
    [editing, update],
  );

  const frame = (id: string) => {
    const instance = board!.widgets.find((w) => w.id === id)!;
    return (
      <WidgetFrame
        instance={instance}
        editing={editing}
        onSettings={() => setSettingsFor(id)}
        onRemove={() => update((b) => removeWidget(b, id))}
      />
    );
  };
  const settingsWidget = board?.widgets.find((w) => w.id === settingsFor);

  return (
    <main ref={containerRef} className={editing ? "board editing" : "board"}>
      {editing && board && (
        <BoardToolbar
          saveState={saveState}
          onAdd={(type) => update((b) => addWidget(b, type))}
          onReset={() => update(() => defaultBoard)}
        />
      )}
      {!board ? (
        <p className="quiet">Loading your board…</p>
      ) : board.widgets.length === 0 ? (
        <p className="quiet board-empty">The board is empty. Choose Edit board, then Add widget.</p>
      ) : !mounted ? null : width < STACK_BELOW ? (
        <div className="stack">
          {stackOrder(board).map((w) => (
            <div key={w.id}>{frame(w.id)}</div>
          ))}
        </div>
      ) : (
        <Responsive
          width={width}
          breakpoints={BREAKPOINTS}
          cols={COLS}
          layouts={layouts}
          rowHeight={ROW_HEIGHT}
          margin={[GAP, GAP]}
          containerPadding={[0, 0]}
          dragConfig={{ enabled: editing, handle: ".widget-drag", cancel: "button" }}
          resizeConfig={{ enabled: editing }}
          onLayoutChange={onLayoutChange}
        >
          {board.widgets.map((w) => (
            <div key={w.id}>{frame(w.id)}</div>
          ))}
        </Responsive>
      )}
      {settingsWidget && (
        <WidgetSettings
          instance={settingsWidget}
          onChange={(next) => update((b) => replaceWidget(b, next))}
          onClose={() => setSettingsFor(null)}
        />
      )}
    </main>
  );
}

function withMinimums(board: BoardState): ResponsiveLayouts {
  const type = new Map(board.widgets.map((w) => [w.id, w.type]));
  const out: ResponsiveLayouts = {};
  for (const [bp, cells] of Object.entries(board.layouts)) {
    const cols = COLS[bp as keyof typeof COLS];
    out[bp] = cells.map((c) => {
      const { minW, minH } = definition(type.get(c.i)!).size;
      return { ...c, minW: Math.min(cols, Math.ceil((minW / 12) * cols)), minH };
    });
  }
  return out;
}

const SAVE_TEXT: Record<SaveState, string> = {
  idle: "",
  saving: "Saving…",
  saved: "Saved",
  failed: "Couldn't save. Your next change will try again.",
};

function BoardToolbar({ saveState, onAdd, onReset }: {
  saveState: SaveState;
  onAdd: (type: WidgetType) => void;
  onReset: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setAdding(false), []);

  return (
    <div className="board-toolbar" role="toolbar" aria-label="Edit board">
      <button ref={anchor} type="button" className="pill-button" aria-expanded={adding} onClick={() => setAdding(!adding)}>
        <PlusIcon /> Add widget
      </button>
      {adding && (
        <Popover anchor={anchor} label="Add widget" onClose={close}>
          <div className="menu widget-menu">
            {(Object.keys(widgets) as WidgetType[]).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => {
                  onAdd(type);
                  close();
                }}
              >
                <b>{widgets[type].title}</b>
                <span>{widgets[type].description}</span>
              </button>
            ))}
          </div>
        </Popover>
      )}
      <button
        type="button"
        className="text-button"
        onClick={() => {
          if (window.confirm("Put the board back to its original widgets and layout? Your item notes and done marks stay.")) onReset();
        }}
      >
        Reset board
      </button>
      <span className="board-hint">Drag a widget by its title bar; resize it from the bottom-right corner.</span>
      <span className={saveState === "failed" ? "save-state problem" : "save-state"} role="status">
        {SAVE_TEXT[saveState]}
      </span>
    </div>
  );
}
