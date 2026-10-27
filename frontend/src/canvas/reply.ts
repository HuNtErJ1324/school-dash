/**
 * Reading the tutor's replies: the chat text, and what to add to the canvas. Kept apart from annotate.ts
 * (which draws it) so the chat can strip canvas blocks without loading Excalidraw.
 */

export type Point = [number, number];

/**
 * What the tutor adds to the canvas, from the fenced ```canvas block that ends its reply (the agent's
 * AGENTS.md, "The shared canvas"). Points are pixels in the snapshot it was looking at.
 */
export type Action =
  | { note: string; at: Point; point_to?: Point }
  | { circle: [number, number, number, number] }
  | { problem: string } // shown pinned over the canvas (CanvasBoard's ProblemCard), with its math rendered
  | { title: string };

const BLOCK = /```canvas[^\n]*\n([\s\S]*?)```/g;

/** A reply's chat text without its canvas block, and the block's actions. Malformed ones are dropped. */
export function parseReply(text: string): { chat: string; actions: Action[] } {
  const actions: Action[] = [];
  const chat = text.replace(BLOCK, (_, body: string) => {
    try {
      const parsed: unknown = JSON.parse(body);
      for (const a of Array.isArray(parsed) ? parsed : [parsed]) {
        const action = valid(a);
        if (action) actions.push(action);
      }
    } catch {
      // not JSON: nothing goes on the canvas
    }
    return "";
  });
  return { chat: chat.trim(), actions };
}

const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isPoint = (v: unknown): v is Point => Array.isArray(v) && v.length === 2 && v.every(isNumber);

function valid(a: unknown): Action | null {
  if (!a || typeof a !== "object") return null;
  const o = a as Record<string, unknown>;
  if (typeof o.note === "string" && o.note.trim() && isPoint(o.at)) {
    return { note: o.note.trim().slice(0, 400), at: o.at, ...(isPoint(o.point_to) && { point_to: o.point_to }) };
  }
  if (Array.isArray(o.circle) && o.circle.length === 4 && o.circle.every(isNumber)) {
    return { circle: o.circle as [number, number, number, number] };
  }
  if (typeof o.problem === "string" && o.problem.trim()) return { problem: o.problem.trim() };
  if (typeof o.title === "string" && o.title.trim()) return { title: o.title.trim().slice(0, 200) };
  return null;
}

/** A reply as the chat shows it: its words, or a word that it drew on the canvas. */
export function chatText(text: string): string {
  return parseReply(text).chat || "*Added to the canvas.*";
}
