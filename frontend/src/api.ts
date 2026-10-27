export type Kind =
  | "assignment" | "quiz" | "exam" | "discussion" | "announcement" | "release"
  | "lecture" | "section" | "office_hours" | "event" | "other";
export type Status = "pending" | "submitted" | "graded" | "missing" | "excused";

export interface Course {
  id: string;
  code: string;
  name: string | null;
  color: string | null;
  links: Record<string, string>;
}

export interface Item {
  id: number;
  source: string;
  source_id: string;
  course: Course | null;
  kind: Kind;
  title: string;
  url: string | null;
  at: string | null;
  due_at: string | null;
  starts_at: string | null;
  ends_at: string | null;
  all_day: boolean;
  location: string | null;
  status: Status | null;
  points: number | null; // points possible
  score: number | null; // points earned, once graded
  category: string | null;
  sources: { source: string; url: string | null }[]; // every source that has this item
  // Your own state. `done` is what you marked; `status` is what the sources say.
  done: boolean;
  hidden: boolean;
  pinned: boolean;
  note: string | null;
  separate: boolean;
}

export interface OverlayPatch {
  done?: boolean;
  hidden?: boolean;
  pinned?: boolean;
  note?: string; // "" clears it
  separate?: boolean;
}

export interface GradedItem {
  id: number;
  title: string;
  url: string | null;
  score: number;
  points: number | null;
  percent: number | null;
  dropped: boolean;
}

export interface CategoryGrade {
  name: string;
  weight: number;
  percent: number | null;
  earned: number;
  possible: number;
  items: GradedItem[];
}

export interface CourseGrades {
  course: Course;
  weighted: boolean;
  estimate: number | null;
  counted_weight: number;
  categories: CategoryGrade[];
  uncategorized: GradedItem[];
  earned: number;
  possible: number;
}

/** A saved board, as the server stores it. The board module gives `widgets` its real type. */
export interface SavedBoard {
  widgets: { id: string; type: string; title?: string; options?: Record<string, unknown> }[];
  layouts: Record<string, { i: string; x: number; y: number; w: number; h: number }[]>;
  offered?: string[]; // widget types already added to this board once (see boardState.ts)
}

export interface SyncRun {
  started_at: string;
  finished_at: string;
  ok: boolean;
  error: string | null;
  items_seen: number;
}

export interface SourceStatus {
  name: string;
  type: string;
  interval_minutes: number | null;
  running: boolean;
  next_run_at: string | null;
  last_run: SyncRun | null;
  last_success_at: string | null;
  config_error: string | null;
}

export interface Meta {
  quarter: string | null; // the current quarter, "26au"
  timezone: string;
  now: string;
}

export interface QuarterInfo {
  code: string; // "26au"
  name: string; // "Autumn 2026"
  courses: string[]; // course ids
}

export interface Quarters {
  current: string;
  quarters: QuarterInfo[]; // newest first
}

/** The filter every widget speaks. Widgets differ in how they draw items, not how they ask. */
export interface ItemQuery {
  start?: Date;
  end?: Date;
  kinds?: Kind[];
  courses?: string[];
  sources?: string[];
  status?: (Status | "none")[];
  pinned?: boolean;
  includeHidden?: boolean;
  quarter?: string; // that quarter's courses (useItems fills in the selected one)
}

export function itemParams(q: ItemQuery): URLSearchParams {
  const p = new URLSearchParams();
  if (q.start) p.set("start", q.start.toISOString());
  if (q.end) p.set("end", q.end.toISOString());
  q.kinds?.forEach((v) => p.append("kind", v));
  q.courses?.forEach((v) => p.append("course", v));
  q.sources?.forEach((v) => p.append("source", v));
  q.status?.forEach((v) => p.append("status", v));
  if (q.pinned !== undefined) p.set("pinned", String(q.pinned));
  if (q.includeHidden) p.set("include_hidden", "true");
  if (q.quarter) p.set("quarter", q.quarter);
  return p;
}

export type JobStatus = "running" | "ready" | "error";

export interface AgentStatus {
  enabled: boolean; // there's an agent container
  ready: boolean; // it answers, with prime-agent and an API key
  problem: string | null; // what to fix when it isn't ready
  model: string;
  busy: boolean;
  next_plan_at: string | null;
  spent_this_month: number;
}

/** A library document (doc_id, with pages) or a web page (url). */
export interface Resource {
  title: string;
  doc_id: number | null;
  pages: string | null;
  url: string | null;
}

export interface PlanTask {
  title: string;
  minutes: number | null;
  course: string | null; // course code
  item_id: number | null; // the item it moves forward
  when: string | null;
  why: string | null;
  steps: string[];
  resources: Resource[];
  done: boolean;
}

export interface Plan {
  id: number;
  date: string;
  status: JobStatus;
  note: string | null;
  summary: string | null;
  tasks: PlanTask[];
  heads_up: string[];
  answer: string | null;
  error: string | null;
  model: string | null;
  cost: number | null;
  seconds: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface TodayPlan {
  plan: Plan | null; // the newest finished plan for today
  pending: Plan | null; // a newer attempt, still running or failed
}

export interface Brief {
  id: number;
  item_id: number;
  title: string;
  status: JobStatus;
  markdown: string | null;
  minutes: number | null;
  resources: Resource[];
  answer: string | null;
  error: string | null;
  cost: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface ItemBrief {
  brief: Brief | null;
  pending: Brief | null;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  status: JobStatus;
  text: string;
  photos: string[]; // ids of attached photos: photoUrl(id)
  canvas: CanvasMessage | null; // sent from the Canvas tab
  error: string | null;
  cost: number | null;
  created_at: string;
}

export interface CanvasMessage {
  kind: CanvasAskKind;
  snapshot?: string; // the upload id of the canvas picture sent with it
}

export type CanvasAskKind = "setup" | "help" | "chat";

export interface Photo {
  id: string;
  width: number;
  height: number;
  size: number;
}

export const photoUrl = (id: string) => `/api/uploads/${id}`;

/** How a tutoring session runs (backend/app/agent/prompts.py MODES). "ask" is a plain question. */
export type TutorMode = "ask" | "explain" | "quiz" | "review";

export interface Conversation {
  id: number;
  title: string;
  item_id: number | null; // the item it's about
  course_id: string | null;
  mode: TutorMode | "canvas"; // canvas: a Canvas tab whiteboard's conversation
  canvas_id: number | null; // the canvas a Tutor session opened beside its chat
  created_at: string;
  updated_at: string;
  messages: Message[];
}

export interface ConversationStart {
  itemId?: number | null;
  courseId?: string | null;
  mode?: TutorMode;
}

export interface LibraryDoc {
  id: number;
  source: "site" | "canvas" | "upload";
  course_id: string | null;
  title: string;
  kind: string;
  url: string | null;
  item_source_id: string | null;
  has_file: boolean;
  has_text: boolean;
  pages: number | null;
  size: number | null;
  note: string | null;
  updated_at: string;
}

export interface LibraryRun {
  name: string; // "site:cse410", "canvas"
  started_at: string;
  finished_at: string | null;
  error: string | null;
  saved: number;
  unchanged: number;
  removed: number;
  skipped: string[];
}

export interface Library {
  documents: LibraryDoc[];
  fetchers: string[];
  runs: LibraryRun[];
  running: boolean;
  next_run_at: string | null;
}

export type UploadKind = "textbook" | "notes" | "assignment" | "slides" | "reading" | "other";

/** A Canvas tab whiteboard (backend/app/api_canvas.py). */
export interface CanvasSummary {
  id: number;
  title: string;
  course_id: string | null;
  conversation_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface CanvasData extends CanvasSummary {
  scene: { elements?: unknown[]; appState?: Record<string, unknown> };
  state: Record<string, unknown>; // the page's own bookkeeping (canvas/CanvasBoard.tsx)
  files: { id: string; mime_type: string }[]; // canvasFileUrl(canvas id, file id)
}

export interface CanvasSnapshot {
  id: string; // an upload
  width: number;
  height: number;
  note: [number, number]; // how much of the picture a tutor's note takes, in its pixels
}

export const canvasFileUrl = (canvasId: number, fileId: string) => `/api/canvases/${canvasId}/files/${fileId}`;

/** Where a citation opens: the PDF at that page, the web page it came from, or its text. */
export function docHref(docId: number, pages?: string | null): string {
  const page = pages?.match(/\d+/)?.[0];
  return `/api/library/${docId}/open${page ? `?page=${page}` : ""}`;
}

export class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  { params, body }: { params?: URLSearchParams; body?: unknown } = {},
): Promise<T> {
  const raw = body instanceof FormData || body instanceof Blob; // sent as is, with its own type
  const res = await fetch(params?.size ? `${path}?${params}` : path, {
    method,
    headers: body === undefined || raw ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
  });
  if (!res.ok) throw new HttpError(await problem(res, `${method} ${path} failed with HTTP ${res.status}`), res.status);
  return (res.status === 204 ? undefined : res.json()) as Promise<T>;
}

/** The server's own explanation, when it gave one ({"detail": "..."}). */
async function problem(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  return fallback;
}

export const api = {
  meta: () => request<Meta>("GET", "/api/meta"),
  quarters: () => request<Quarters>("GET", "/api/quarters"),
  courses: (active = true, quarter?: string | null) =>
    request<Course[]>("GET", "/api/courses", {
      params: new URLSearchParams({ active: String(active), ...(quarter && { quarter }) }),
    }),
  items: (q: ItemQuery) => request<Item[]>("GET", "/api/items", { params: itemParams(q) }),
  patchOverlay: (id: number, patch: OverlayPatch) => request<Item>("PATCH", `/api/items/${id}/overlay`, { body: patch }),
  grades: (quarter?: string | null) =>
    request<CourseGrades[]>("GET", "/api/grades", { params: new URLSearchParams(quarter ? { quarter } : {}) }),
  sources: () => request<SourceStatus[]>("GET", "/api/sources"),
  syncAll: () => request<SourceStatus[]>("POST", "/api/sync"),
  /** null when nothing has been saved yet. */
  board: async (name: string): Promise<SavedBoard | null> => {
    try {
      return await request<SavedBoard>("GET", `/api/boards/${name}`);
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) return null;
      throw e;
    }
  },
  saveBoard: (name: string, board: SavedBoard) => request<SavedBoard>("PUT", `/api/boards/${name}`, { body: board }),

  agent: () => request<AgentStatus>("GET", "/api/agent"),
  todayPlan: () => request<TodayPlan>("GET", "/api/plans/today"),
  makePlan: (note: string) => request<Plan>("POST", "/api/plans", { body: { note } }),
  setTaskDone: (planId: number, index: number, done: boolean) =>
    request<Plan>("PATCH", `/api/plans/${planId}/tasks/${index}`, { body: { done } }),
  briefs: () => request<Brief[]>("GET", "/api/briefs"),
  itemBrief: (itemId: number) => request<ItemBrief>("GET", `/api/items/${itemId}/brief`),
  writeBrief: (itemId: number) => request<Brief>("POST", `/api/items/${itemId}/brief`),
  conversations: () => request<Conversation[]>("GET", "/api/conversations"),
  conversation: (id: number) => request<Conversation>("GET", `/api/conversations/${id}`),
  startConversation: (text: string, { itemId, courseId, mode = "ask" }: ConversationStart = {}, photos: string[] = []) =>
    request<Conversation>("POST", "/api/conversations", {
      body: { text, item_id: itemId ?? null, course_id: courseId ?? null, mode, photos },
    }),
  sendMessage: (id: number, text: string, photos: string[] = []) =>
    request<Conversation>("POST", `/api/conversations/${id}/messages`, { body: { text, photos } }),
  uploadPhoto: (file: File) => {
    const form = new FormData();
    form.set("file", file);
    return request<Photo>("POST", "/api/uploads", { body: form });
  },
  deleteConversation: (id: number) => request<void>("DELETE", `/api/conversations/${id}`),

  canvases: () => request<CanvasSummary[]>("GET", "/api/canvases"),
  canvas: (id: number) => request<CanvasData>("GET", `/api/canvases/${id}`),
  /** A Tutor session's canvas, made the first time. */
  sessionCanvas: (conversationId: number) => request<CanvasData>("POST", `/api/conversations/${conversationId}/canvas`),
  createCanvas: (body: { title?: string; course_id: string | null }) => request<CanvasData>("POST", "/api/canvases", { body }),
  renameCanvas: (id: number, title: string) => request<CanvasSummary>("PATCH", `/api/canvases/${id}`, { body: { title } }),
  saveCanvasScene: (id: number, body: { elements: unknown[]; appState: Record<string, unknown>; state: Record<string, unknown> }) =>
    request<void>("PUT", `/api/canvases/${id}/scene`, { body }),
  saveCanvasFile: (id: number, fileId: string, blob: Blob) => request<unknown>("PUT", canvasFileUrl(id, fileId), { body: blob }),
  deleteCanvas: (id: number) => request<void>("DELETE", `/api/canvases/${id}`),
  askCanvas: (id: number, body: { kind: CanvasAskKind; text?: string; photos?: string[]; snapshot?: CanvasSnapshot }) =>
    request<Conversation>("POST", `/api/canvases/${id}/ask`, { body }),

  library: () => request<Library>("GET", "/api/library"),
  syncLibrary: () => request<{ running: boolean }>("POST", "/api/library/sync"),
  upload: (file: File, fields: { course_id: string; kind: UploadKind; title: string }) => {
    const form = new FormData();
    form.set("file", file);
    for (const [k, v] of Object.entries(fields)) if (v) form.set(k, v);
    return request<LibraryDoc>("POST", "/api/library/upload", { body: form });
  },
  addText: (body: { title: string; text: string; course_id: string | null; kind: UploadKind }) =>
    request<LibraryDoc>("POST", "/api/library/text", { body }),
  deleteDoc: (id: number) => request<void>("DELETE", `/api/library/${id}`),
};
