import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type SavedBoard } from "../api";
import { definition, isWidgetType, type WidgetType } from "../widgets/registry";

export const BREAKPOINTS = { lg: 1000, md: 0 } as const; // grid container widths in px
export const COLS = { lg: 12, md: 8 } as const;
export const STACK_BELOW = 720; // narrower than this, widgets stack in one column at natural height
export type Breakpoint = keyof typeof COLS;

export interface WidgetInstance {
  id: string;
  type: WidgetType;
  title?: string;
  options?: Record<string, unknown>;
}

export interface Cell {
  i: string; // widget id
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface BoardState {
  widgets: WidgetInstance[];
  layouts: Record<Breakpoint, Cell[]>;
  offered: string[]; // widget types this board has been given once (see NEW_WIDGETS)
}

/**
 * Widgets added since boards were first saved. A saved board gets each one once, the plan at
 * the top and the rest at the bottom; after that, removing one sticks.
 */
const NEW_WIDGETS: WidgetType[] = ["plan", "ask", "library"];

export const defaultBoard: BoardState = {
  widgets: [
    { id: "plan", type: "plan" },
    { id: "up-next", type: "up-next" },
    { id: "ask", type: "ask" },
    { id: "today", type: "today" },
    { id: "week", type: "week" },
    { id: "courses", type: "courses" },
    { id: "grades", type: "grades" },
    { id: "library", type: "library" },
  ],
  layouts: {
    lg: [
      { i: "plan", x: 0, y: 0, w: 7, h: 13 },
      { i: "up-next", x: 7, y: 0, w: 5, h: 13 },
      { i: "ask", x: 0, y: 13, w: 7, h: 10 },
      { i: "today", x: 7, y: 13, w: 5, h: 10 },
      { i: "week", x: 0, y: 23, w: 12, h: 9 },
      { i: "courses", x: 0, y: 32, w: 12, h: 7 },
      { i: "grades", x: 0, y: 39, w: 6, h: 9 },
      { i: "library", x: 6, y: 39, w: 6, h: 9 },
    ],
    md: [
      { i: "plan", x: 0, y: 0, w: 8, h: 13 },
      { i: "up-next", x: 0, y: 13, w: 5, h: 10 },
      { i: "today", x: 5, y: 13, w: 3, h: 10 },
      { i: "ask", x: 0, y: 23, w: 8, h: 10 },
      { i: "week", x: 0, y: 33, w: 8, h: 9 },
      { i: "courses", x: 0, y: 42, w: 8, h: 8 },
      { i: "grades", x: 0, y: 50, w: 8, h: 9 },
      { i: "library", x: 0, y: 59, w: 8, h: 9 },
    ],
  },
  offered: NEW_WIDGETS,
};

/** A cell for a widget at the bottom of a layout, at its default size scaled to the column count. */
function cellAtBottom(cells: Cell[], id: string, type: WidgetType, bp: Breakpoint): Cell {
  const { w, h } = definition(type).size;
  const cols = COLS[bp];
  return { i: id, x: 0, y: Math.max(0, ...cells.map((c) => c.y + c.h)), w: Math.min(cols, Math.round((w / 12) * cols) || 1), h };
}

/** A widget across the top of every layout, everything else moved down to make room. */
function addAtTop(board: BoardState, type: WidgetType): BoardState {
  const { h } = definition(type).size;
  const layouts = { ...board.layouts };
  for (const bp of Object.keys(COLS) as Breakpoint[]) {
    layouts[bp] = [{ i: type, x: 0, y: 0, w: COLS[bp], h }, ...layouts[bp].map((c) => ({ ...c, y: c.y + h }))];
  }
  return { ...board, widgets: [{ id: type, type }, ...board.widgets], layouts };
}

/** Give a saved board the widgets it hasn't been offered yet. `changed` says whether to save it. */
export function offerNewWidgets(board: BoardState): { board: BoardState; changed: boolean } {
  let next = board;
  for (const type of NEW_WIDGETS) {
    if (next.offered.includes(type)) continue;
    if (!next.widgets.some((w) => w.type === type)) {
      next = type === "plan" && !next.widgets.some((w) => w.id === type) ? addAtTop(next, type) : addWidget(next, type);
    }
    next = { ...next, offered: [...next.offered, type] };
  }
  return { board: next, changed: next !== board };
}

/** Make a saved board usable: drop widget types that no longer exist, place any widget that has no cell. */
export function normalize(saved: SavedBoard | null): BoardState {
  if (!saved) return defaultBoard;
  const seen = new Set<string>();
  const widgets = saved.widgets.filter((w): w is WidgetInstance => {
    if (!isWidgetType(w.type) || seen.has(w.id)) return false;
    seen.add(w.id);
    return true;
  });
  const layouts = {} as Record<Breakpoint, Cell[]>;
  for (const bp of Object.keys(COLS) as Breakpoint[]) {
    const cells = (saved.layouts[bp] ?? []).filter((c) => seen.has(c.i)).map(({ i, x, y, w, h }) => ({ i, x, y, w, h }));
    for (const widget of widgets) {
      if (!cells.some((c) => c.i === widget.id)) cells.push(cellAtBottom(cells, widget.id, widget.type, bp));
    }
    layouts[bp] = cells;
  }
  return { widgets, layouts, offered: saved.offered ?? [] };
}

export function addWidget(board: BoardState, type: WidgetType): BoardState {
  const id = `${type}-${Math.random().toString(36).slice(2, 8)}`;
  const layouts = { ...board.layouts };
  for (const bp of Object.keys(COLS) as Breakpoint[]) layouts[bp] = [...layouts[bp], cellAtBottom(layouts[bp], id, type, bp)];
  return { ...board, widgets: [...board.widgets, { id, type }], layouts };
}

export function removeWidget(board: BoardState, id: string): BoardState {
  const layouts = { ...board.layouts };
  for (const bp of Object.keys(COLS) as Breakpoint[]) layouts[bp] = layouts[bp].filter((c) => c.i !== id);
  return { ...board, widgets: board.widgets.filter((w) => w.id !== id), layouts };
}

export function replaceWidget(board: BoardState, next: WidgetInstance): BoardState {
  return { ...board, widgets: board.widgets.map((w) => (w.id === next.id ? next : w)) };
}

/** Take positions from the grid, keeping the board as-is when nothing actually moved. */
export function withLayouts(board: BoardState, layouts: Partial<Record<string, readonly Cell[]>>): BoardState {
  const next = { ...board.layouts };
  let changed = false;
  for (const bp of Object.keys(COLS) as Breakpoint[]) {
    const incoming = layouts[bp];
    if (!incoming) continue;
    const cells = incoming.map(({ i, x, y, w, h }) => ({ i, x, y, w, h }));
    if (JSON.stringify(cells) !== JSON.stringify(board.layouts[bp])) {
      next[bp] = cells;
      changed = true;
    }
  }
  return changed ? { ...board, layouts: next } : board;
}

/** Reading order of the wide layout: how widgets stack on a phone. */
export function stackOrder(board: BoardState): WidgetInstance[] {
  const pos = new Map(board.layouts.lg.map((c) => [c.i, c]));
  const key = (w: WidgetInstance) => pos.get(w.id) ?? { y: Infinity, x: 0 };
  return [...board.widgets].sort((a, b) => key(a).y - key(b).y || key(a).x - key(b).x);
}

export type SaveState = "idle" | "saving" | "saved" | "failed";

/** The board, loaded from the server and saved back (debounced) whenever it changes. */
export function useBoard(name = "default") {
  const query = useQuery({ queryKey: ["board", name], queryFn: () => api.board(name), staleTime: Infinity, retry: 1 });
  const [board, setBoard] = useState<BoardState | null>(null);
  const current = useRef<BoardState | null>(null);
  const unsaved = useRef(false);
  const save = useMutation({ mutationFn: (b: BoardState) => api.saveBoard(name, b) });

  // Load once. If the server can't be reached, start from the default and save nothing until you edit.
  useEffect(() => {
    if (current.current || query.isPending) return;
    const { board: loaded, changed } = offerNewWidgets(normalize(query.data ?? null));
    current.current = loaded;
    unsaved.current = changed && !!query.data; // a saved board just got new widgets: keep them
    setBoard(loaded);
  }, [query.isPending, query.data]);

  const update = useCallback((change: (b: BoardState) => BoardState) => {
    if (!current.current) return;
    const next = change(current.current);
    if (next === current.current) return;
    current.current = next;
    unsaved.current = true;
    setBoard(next);
  }, []);

  useEffect(() => {
    if (!board || !unsaved.current) return;
    const timer = setTimeout(() => {
      unsaved.current = false;
      save.mutate(board);
    }, 500);
    return () => clearTimeout(timer);
  }, [board, save.mutate]);

  const saveState: SaveState = save.isPending ? "saving" : save.isError ? "failed" : save.isSuccess ? "saved" : "idle";
  return { board, update, saveState };
}
