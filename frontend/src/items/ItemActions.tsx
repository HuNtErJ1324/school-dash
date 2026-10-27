import { useCallback, useRef, useState } from "react";

import type { Item, OverlayPatch } from "../api";
import { BriefDialog } from "../agent/BriefDialog";
import { useOverlay } from "../hooks";
import { BookIcon, ChatIcon, CheckIcon, DotsIcon, HideIcon, NoteIcon, OpenIcon, PinIcon, SplitIcon } from "../ui/icons";
import { Popover } from "../ui/Popover";
import { DEADLINE_KINDS, DONE } from "../widgets/parts";

const SOURCE_SAYS: Record<string, string> = { submitted: "submitted", graded: "graded", excused: "excused" };

/** The tick circle: one tap marks an item done. Locked when the source already says it's done. */
export function DoneToggle({ item, large = false }: { item: Item; large?: boolean }) {
  const overlay = useOverlay();
  const bySource = DONE.has(item.status);
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={item.done || bySource}
      aria-label={`Done: ${item.title}`}
      title={bySource ? `Marked ${SOURCE_SAYS[item.status!]} by ${item.source}` : undefined}
      disabled={bySource}
      className={large ? "tick tick-large" : "tick"}
      onClick={() => overlay.mutate({ id: item.id, patch: { done: !item.done } })}
    >
      <CheckIcon />
    </button>
  );
}

/** A name for where a link goes: "Gradescope", "Canvas", "Course site". */
export function siteName(url: string | null, fallback: string): string {
  if (!url) return fallback;
  let host: string;
  try {
    host = new URL(url).hostname;
  } catch {
    return fallback;
  }
  if (host.endsWith("gradescope.com")) return "Gradescope";
  if (host.startsWith("canvas.")) return "Canvas";
  if (host.endsWith("edstem.org")) return "Ed";
  if (host === "courses.cs.washington.edu") return "Course site";
  return host.replace(/^www\./, "");
}

/** Where else this item lives, when more than one site has it. */
function otherSites(item: Item): { name: string; url: string }[] {
  const seen = new Map<string, string>();
  for (const s of item.sources) {
    const name = siteName(s.url, s.source);
    if (s.url && !seen.has(name)) seen.set(name, s.url);
  }
  return seen.size > 1 ? [...seen].map(([name, url]) => ({ name, url })) : [];
}

/** "⋯" menu: the agent's brief, open it elsewhere, pin, note, hide, and un-merge. */
export function ItemMenu({ item }: { item: Item }) {
  const [open, setOpen] = useState(false);
  const [editingNote, setEditingNote] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const isWork = DEADLINE_KINDS.includes(item.kind);
  const anchor = useRef<HTMLButtonElement>(null);
  const overlay = useOverlay();
  const close = useCallback(() => {
    setOpen(false);
    setEditingNote(false);
  }, []);
  const apply = (patch: OverlayPatch) => {
    overlay.mutate({ id: item.id, patch });
    close();
  };

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="icon-button"
        aria-label={`Options for ${item.title}`}
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <DotsIcon />
      </button>
      {open && (
        <Popover anchor={anchor} label={`Options for ${item.title}`} onClose={close}>
          {editingNote ? (
            <NoteEditor item={item} onSave={(note) => apply({ note })} onCancel={() => setEditingNote(false)} />
          ) : (
            <div className="menu">
              <button
                type="button"
                onClick={() => {
                  close();
                  setBriefing(true);
                }}
              >
                {isWork ? <BookIcon /> : <ChatIcon />} {isWork ? "Brief and questions" : "Ask about this"}
              </button>
              {otherSites(item).map(({ name, url }) => (
                <a key={name} href={url} target="_blank" rel="noreferrer" onClick={close}>
                  <OpenIcon /> Open in {name}
                </a>
              ))}
              <button type="button" onClick={() => apply({ pinned: !item.pinned })}>
                <PinIcon /> {item.pinned ? "Unpin" : "Pin to the top"}
              </button>
              <button type="button" onClick={() => setEditingNote(true)}>
                <NoteIcon /> {item.note ? "Edit note" : "Add a note"}
              </button>
              <button type="button" onClick={() => apply({ hidden: !item.hidden })}>
                <HideIcon /> {item.hidden ? "Unhide" : "Hide"}
              </button>
              {item.sources.length > 1 && (
                <button type="button" title="These were matched as the same work. Show each site's copy on its own." onClick={() => apply({ separate: true })}>
                  <SplitIcon /> Keep these separate
                </button>
              )}
              {item.separate && (
                <button type="button" onClick={() => apply({ separate: false })}>
                  <SplitIcon /> Let it merge with matches again
                </button>
              )}
            </div>
          )}
        </Popover>
      )}
      {briefing && (
        <BriefDialog
          itemId={item.id}
          title={item.course ? `${item.course.code}: ${item.title}` : item.title}
          withBrief={isWork}
          onClose={() => setBriefing(false)}
        />
      )}
    </>
  );
}

function NoteEditor({ item, onSave, onCancel }: { item: Item; onSave: (note: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(item.note ?? "");
  return (
    <form
      className="note-editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(text);
      }}
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSave(text);
        }}
        rows={3}
        maxLength={2000}
        autoFocus
        aria-label={`Note for ${item.title}`}
        placeholder="Only Q4 left, ask at OH…"
      />
      <div className="note-editor-actions">
        <button type="button" className="text-button" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="pill-button">
          Save note
        </button>
      </div>
    </form>
  );
}

export function NoteLine({ note }: { note: string | null }) {
  if (!note) return null;
  return (
    <p className="note">
      <NoteIcon /> <span>{note}</span>
    </p>
  );
}
