import { useEffect, useId, useRef, useState } from "react";

import { docHref, type LibraryDoc, type LibraryRun, type UploadKind } from "../api";
import { useLibrary, useLibraryChange } from "../agent/hooks";
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

/** Add a file (a textbook PDF, a spec from the lab repo) or paste text (what the TA said, exam topics). */
function AddDialog({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const courses = useCourses();
  const { upload, addText } = useLibraryChange();
  const [mode, setMode] = useState<"file" | "text">("file");
  const [courseId, setCourseId] = useState("");
  const [kind, setKind] = useState<UploadKind>("textbook");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const pending = upload.isPending || addText.isPending;
  const error = upload.error ?? addText.error;

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const submit = () => {
    const done = { onSuccess: () => ref.current?.close() };
    if (mode === "file" && file) upload.mutate({ file, course_id: courseId, kind, title: title.trim() }, done);
    if (mode === "text" && title.trim() && text.trim())
      addText.mutate({ title: title.trim(), text, course_id: courseId || null, kind }, done);
  };
  const canSubmit = !pending && (mode === "file" ? !!file : !!title.trim() && !!text.trim());

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
          {(["file", "text"] as const).map((m) => (
            <label key={m}>
              <input
                type="radio"
                name="mode"
                checked={mode === m}
                onChange={() => {
                  setMode(m);
                  if (m === "text" && kind === "textbook") setKind("notes");
                }}
              />
              {m === "file" ? "A file" : "Text"}
            </label>
          ))}
        </div>
        {mode === "file" ? (
          <label className="field">
            <span className="field-label">File</span>
            <input type="file" accept=".pdf,.docx,.pptx,.txt,.md,.html,.c,.h" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <span className="field-help">PDF, Word, PowerPoint or text, up to 300 MB.</span>
          </label>
        ) : (
          <label className="field">
            <span className="field-label">Text</span>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="The midterm covers chapters 1 to 6; no proofs from section 4.3…" />
          </label>
        )}
        <label className="field">
          <span className="field-label">Title{mode === "file" ? " (optional)" : ""}</span>
          <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder={mode === "file" ? file?.name ?? "" : "Midterm topics"} />
        </label>
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
        <div className="settings-actions">
          <button type="button" className="text-button" onClick={() => ref.current?.close()}>
            Cancel
          </button>
          <button type="submit" className="pill-button" disabled={!canSubmit}>
            {pending ? (mode === "file" ? "Uploading and reading…" : "Adding…") : "Add"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
