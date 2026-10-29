import { useEffect, useId, useRef, useState } from "react";

import { docHref, type LibraryDoc, type LibraryRun, type UploadKind } from "../api";
import { useLibrary, useLibraryChange, type UploadFile } from "../agent/hooks";
import { courseStyle } from "../courses";
import { useCourses, useNow } from "../hooks";
import { useQuarter } from "../quarter";
import { ago } from "../time";
import { CloseIcon, TrashIcon, UploadIcon } from "../ui/icons";

export interface LibraryOptions {
  courses: string[]; // empty means every course
}

export const libraryDefaults: LibraryOptions = { courses: [] };

// Singular and plural, in the order documents are listed: yours first, reference manuals last.
const KINDS: [string, string, string][] = [
  ["textbook", "textbook", "textbooks"],
  ["notes", "note", "notes"],
  ["assignment", "assignment", "assignments"],
  ["syllabus", "syllabus", "syllabi"],
  ["slides", "slide deck", "slide decks"],
  ["exam prep", "exam prep", "exam prep"],
  ["reading", "reading", "readings"],
  ["page", "page", "pages"],
  ["file", "file", "files"],
  ["other", "other", "other"],
  ["reference", "reference", "reference"],
];
const KIND_ORDER = new Map(KINDS.map(([kind], i) => [kind, i]));
const kindRank = (kind: string) => KIND_ORDER.get(kind) ?? KINDS.length;

const FETCHER_NAME = (name: string) => (name === "canvas" ? "Canvas" : name.startsWith("site:") ? "Course site" : name);

/** The course documents the agent reads: from course sites, Canvas, and what you add. */
export function Library({ options }: { options: LibraryOptions }) {
  const library = useLibrary();
  const courses = useCourses();
  const { isCurrent } = useQuarter();
  const { sync } = useLibraryChange();
  const [adding, setAdding] = useState(false);

  if (library.isPending) return <p className="quiet">Loading…</p>;
  if (library.isError) return <p className="problem">Can't reach the dashboard server.</p>;

  const inQuarter = new Set((courses.data ?? []).map((c) => c.id));
  const docs = library.data.documents.filter(
    (d) =>
      (!d.course_id || inQuarter.has(d.course_id)) &&
      (!options.courses.length || (d.course_id && options.courses.includes(d.course_id))),
  );
  const groups = new Map<string, LibraryDoc[]>();
  const sorted = [...docs].sort((a, b) => kindRank(a.kind) - kindRank(b.kind) || a.title.localeCompare(b.title));
  for (const d of sorted) groups.set(d.course_id ?? "", [...(groups.get(d.course_id ?? "") ?? []), d]);
  const course = (id: string) => (courses.data ?? []).find((c) => c.id === id) ?? null;

  return (
    <div className="library">
      <div className="library-bar">
        <button type="button" className="pill-button" onClick={() => setAdding(true)}>
          <UploadIcon /> Add to library
        </button>
        <button type="button" className="text-button" disabled={library.data.running || sync.isPending} onClick={() => sync.mutate()}>
          {library.data.running ? "Checking for new documents…" : "Check for new documents"}
        </button>
      </div>
      <Runs runs={library.data.runs} />
      {groups.size === 0 && (
        <p className="quiet">
          {isCurrent
            ? "Nothing yet. The course site and Canvas are read a minute after the server starts. Add a textbook or your own notes with Add to library."
            : "No documents for this quarter's courses."}
        </p>
      )}
      {[...groups].map(([id, list]) => (
        <details key={id} className="library-course" style={courseStyle(course(id))}>
          <summary>
            <span className="course-code">{course(id)?.code ?? (id || "Not tied to a course")}</span>
            <span className="library-counts">{counts(list)}</span>
          </summary>
          <ul className="library-docs">
            {list.map((d) => (
              <DocRow key={d.id} doc={d} />
            ))}
          </ul>
        </details>
      ))}
      {adding && <AddDialog onClose={() => setAdding(false)} />}
    </div>
  );
}

function counts(list: LibraryDoc[]): string {
  const by = new Map<string, number>();
  for (const d of list) by.set(d.kind, (by.get(d.kind) ?? 0) + 1);
  return [...by]
    .map(([kind, n]) => {
      const [, one, many] = KINDS.find(([k]) => k === kind) ?? [kind, kind, kind];
      return `${n} ${n === 1 ? one : many}`;
    })
    .join(" · ");
}

function Runs({ runs }: { runs: LibraryRun[] }) {
  const now = useNow();
  if (!runs.length) return null;
  return (
    <ul className="library-runs">
      {runs.map((r) => (
        <li key={r.name} className={r.error ? "problem" : undefined}>
          {FETCHER_NAME(r.name)}:{" "}
          {r.error
            ? r.error
            : `checked ${r.finished_at ? ago(now - Date.parse(r.finished_at)) : "now"}${r.saved ? `, ${r.saved} new or changed` : ""}`}
          {r.skipped.length > 0 && <span title={r.skipped.join("\n")}> ({r.skipped.length} skipped)</span>}
        </li>
      ))}
    </ul>
  );
}

function DocRow({ doc }: { doc: LibraryDoc }) {
  const { remove } = useLibraryChange();
  return (
    <li>
      <a href={docHref(doc.id)} target="_blank" rel="noreferrer">
        {doc.title}
      </a>
      <span className="library-doc-meta">
        {doc.kind}
        {doc.pages ? `, ${doc.pages} pp.` : ""}
        {doc.source === "upload" ? ", added by you" : ""}
        {doc.note ? `. ${doc.note}` : ""}
      </span>
      {doc.source === "upload" && (
        <button
          type="button"
          className="icon-button"
          aria-label={`Remove ${doc.title}`}
          disabled={remove.isPending}
          onClick={() => {
            if (confirm(`Remove “${doc.title}” from the library?`)) remove.mutate(doc.id);
          }}
        >
          <TrashIcon />
        </button>
      )}
    </li>
  );
}

const UPLOAD_KINDS: { value: UploadKind; label: string }[] = [
  { value: "textbook", label: "Textbook" },
  { value: "notes", label: "Notes" },
  { value: "assignment", label: "Assignment or spec" },
  { value: "slides", label: "Slides" },
  { value: "reading", label: "Reading" },
  { value: "other", label: "Something else" },
];

// What the library can read (backend/app/library/extract.py); a folder's other files are left out.
const DOC_EXTENSIONS = [".pdf", ".docx", ".pptx", ".txt", ".md", ".html", ".htm", ".c", ".h"];
const MAX_UPLOAD_MB = 300;
const SKIPPED_FOLDERS = new Set(["node_modules", "__pycache__"]);

interface Picked {
  docs: UploadFile[];
  skipped: string[]; // paths the library can't read, or too big
}

const NOTHING: Picked = { docs: [], skipped: [] };

/** The documents in a pick of files or a folder, each titled by its path inside the folder. Hidden folders (.git)
 * and dependencies are left out quietly; files left out for their type or size are listed. */
function pickDocs(list: FileList | null): Picked {
  const docs: UploadFile[] = [];
  const skipped: string[] = [];
  for (const file of Array.from(list ?? [])) {
    const parts = (file.webkitRelativePath || file.name).split("/");
    const inside = parts.length > 1 ? parts.slice(1) : parts; // below the folder you picked
    if (inside.some((p) => p.startsWith(".") || SKIPPED_FOLDERS.has(p))) continue;
    const path = inside.join("/");
    const dot = file.name.lastIndexOf(".");
    const ext = dot > 0 ? file.name.slice(dot).toLowerCase() : "";
    if (!DOC_EXTENSIONS.includes(ext)) skipped.push(path);
    else if (!file.size) skipped.push(`${path} (empty)`);
    else if (file.size > MAX_UPLOAD_MB * 2 ** 20) skipped.push(`${path} (over ${MAX_UPLOAD_MB} MB)`);
    else docs.push({ file, title: path.slice(0, path.length - ext.length) });
  }
  return { docs: docs.sort((a, b) => a.title.localeCompare(b.title)), skipped };
}

/** "a, b, c and 4 more" */
function someOf(names: string[], shown = 4): string {
  return names.length <= shown + 1 ? names.join(", ") : `${names.slice(0, shown).join(", ")} and ${names.length - shown} more`;
}

/** Add files or a whole folder (a textbook PDF, the specs from the lab repo) or paste text (what the TA said, exam
 * topics). */
function AddDialog({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const stop = useRef<AbortController | null>(null);
  const headingId = useId();
  const courses = useCourses();
  const { upload, addText } = useLibraryChange();
  const [mode, setMode] = useState<"files" | "folder" | "text">("files");
  const [courseId, setCourseId] = useState("");
  const [kind, setKind] = useState<UploadKind>("textbook");
  const [title, setTitle] = useState("");
  const [picked, setPicked] = useState<Picked>(NOTHING);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState<string[]>([]);
  const [text, setText] = useState("");
  const pending = upload.isPending || addText.isPending;
  const error = addText.error;
  const count = picked.docs.length;
  const single = mode === "files" && count <= 1; // only then does a title apply

  useEffect(() => {
    ref.current?.showModal();
    const controller = new AbortController();
    stop.current = controller;
    return () => controller.abort(); // closing stops a folder's upload after the file it's on
  }, []);

  const submit = () => {
    if (mode !== "text" && count) {
      const files = single && title.trim() ? [{ ...picked.docs[0], title: title.trim() }] : picked.docs;
      setProgress(0);
      setFailed([]);
      upload.mutate(
        { files, course_id: courseId, kind, onProgress: setProgress, signal: stop.current?.signal },
        {
          onSuccess: (left) => {
            if (!left.length) return ref.current?.close();
            setPicked({ docs: left, skipped: [] }); // Add again sends just these
            setFailed(left.map((f) => `${f.title}: ${f.error}`));
          },
        },
      );
    }
    if (mode === "text" && title.trim() && text.trim())
      addText.mutate({ title: title.trim(), text, course_id: courseId || null, kind }, { onSuccess: () => ref.current?.close() });
  };
  const canSubmit = !pending && (mode === "text" ? !!title.trim() && !!text.trim() : count > 0);
  const plural = (n: number) => `${n} file${n === 1 ? "" : "s"}`;

  return (
    <dialog ref={ref} className="settings" aria-labelledby={headingId} onClose={onClose}>
      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <header className="sheet-head sheet-head-flush">
          <h2 id={headingId} className="sheet-title">
            Add to the library
          </h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={() => ref.current?.close()}>
            <CloseIcon />
          </button>
        </header>
        <p className="field-help">
          The agent reads everything in the library when it plans, writes briefs and answers questions. Add what the course
          sites don't have: your textbook, lab specs from the course repository, your notes.
        </p>
        <div className="segmented" role="radiogroup" aria-label="What to add">
          {(["files", "folder", "text"] as const).map((m) => (
            <label key={m}>
              <input
                type="radio"
                name="mode"
                checked={mode === m}
                disabled={pending}
                onChange={() => {
                  setMode(m);
                  setPicked(NOTHING);
                  setFailed([]);
                  if (m === "text" && kind === "textbook") setKind("notes");
                }}
              />
              {{ files: "Files", folder: "A folder", text: "Text" }[m]}
            </label>
          ))}
        </div>
        {mode !== "text" ? (
          <div className="field">
            <label className="field">
              <span className="field-label">{mode === "files" ? "Files" : "Folder"}</span>
              <input
                key={mode}
                type="file"
                multiple
                accept={mode === "files" ? DOC_EXTENSIONS.join(",") : undefined}
                // Not in React's types; Safari, Chrome and Firefox all pick a folder with it.
                ref={(el) => {
                  if (mode === "folder") el?.setAttribute("webkitdirectory", "");
                }}
                disabled={pending}
                onChange={(e) => {
                  setPicked(pickDocs(e.target.files));
                  setFailed([]);
                }}
              />
            </label>
            <span className="field-help">
              {mode === "files"
                ? `PDF, Word, PowerPoint or text, up to ${MAX_UPLOAD_MB} MB each. Pick as many as you like.`
                : `Every PDF, Word, PowerPoint and text file in it and its subfolders, up to ${MAX_UPLOAD_MB} MB each, ` +
                  "titled by its path in the folder. Hidden folders like .git are left out."}
            </span>
            {(mode === "folder" || count > 1 || picked.skipped.length > 0) && (count > 0 || picked.skipped.length > 0) && (
              <span className="field-help" title={[...picked.docs.map((d) => d.title), ...picked.skipped].join("\n")}>
                {count ? `${plural(count)} to add: ${someOf(picked.docs.map((d) => d.title))}.` : "Nothing in it the library can read."}
                {picked.skipped.length > 0 && ` Leaving out ${plural(picked.skipped.length)}: ${someOf(picked.skipped)}.`}
              </span>
            )}
          </div>
        ) : (
          <label className="field">
            <span className="field-label">Text</span>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="The midterm covers chapters 1 to 6; no proofs from section 4.3…" />
          </label>
        )}
        {(mode === "text" || single) && (
          <label className="field">
            <span className="field-label">Title{mode === "files" ? " (optional)" : ""}</span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              placeholder={mode === "files" ? (picked.docs[0]?.title ?? "") : "Midterm topics"}
            />
          </label>
        )}
        <label className="field">
          <span className="field-label">Course</span>
          <select value={courseId} onChange={(e) => setCourseId(e.target.value)}>
            <option value="">Not tied to a course</option>
            {(courses.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.code}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">What it is</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as UploadKind)}>
            {UPLOAD_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        {error && <p className="problem">{error.message}</p>}
        {failed.length > 0 && (
          <div className="problem">
            Couldn't add {plural(failed.length)}; Add tries {failed.length === 1 ? "it" : "them"} again.
            <ul className="upload-failures">
              {failed.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="settings-actions">
          <button type="button" className="text-button" onClick={() => ref.current?.close()}>
            {upload.isPending ? "Stop" : "Cancel"}
          </button>
          <button type="submit" className="pill-button" disabled={!canSubmit}>
            {addText.isPending
              ? "Adding…"
              : upload.isPending
                ? `Uploading and reading${count > 1 ? ` ${Math.min(progress + 1, count)} of ${count}` : ""}…`
                : count > 1
                  ? `Add ${plural(count)}`
                  : "Add"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
