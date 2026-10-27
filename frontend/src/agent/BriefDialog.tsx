import { useEffect, useId, useRef, useState } from "react";

import { useTimezone } from "../hooks";
import { clock, shortDay } from "../time";
import { CloseIcon } from "../ui/icons";
import { Chat } from "./Chat";
import { useAgent, useItemBrief, useWriteBrief } from "./hooks";
import { duration, Markdown, Resources } from "./Markdown";

/**
 * An item's brief from the agent (what it asks, what to read first, a plan of attack), and
 * questions about it. Without `withBrief`, just the questions: for lectures and the like.
 */
export function BriefDialog({ itemId, title, withBrief = true, onClose }: {
  itemId: number;
  title: string;
  withBrief?: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const [conversation, setConversation] = useState<number | null>(null);

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    <dialog ref={ref} className="sheet" aria-labelledby={headingId} onClose={onClose}>
      <header className="sheet-head">
        <h2 id={headingId} className="sheet-title">
          {title}
        </h2>
        <button type="button" className="icon-button" aria-label="Close" onClick={() => ref.current?.close()}>
          <CloseIcon />
        </button>
      </header>
      <div className="sheet-body">
        {withBrief && <BriefBody itemId={itemId} />}
        <section className={withBrief ? "sheet-section" : undefined}>
          {withBrief && <h3 className="group-label">Ask about it</h3>}
          <Chat
            conversationId={conversation}
            onStarted={setConversation}
            itemId={itemId}
            placeholder="What's the difference between part A and B? Where's this covered in lecture?"
          />
        </section>
      </div>
    </dialog>
  );
}

function BriefBody({ itemId }: { itemId: number }) {
  const tz = useTimezone();
  const agent = useAgent();
  const query = useItemBrief(itemId);
  const write = useWriteBrief(itemId);

  if (query.isPending) return <p className="quiet">Loading…</p>;
  if (query.isError) return <p className="problem">Can't reach the dashboard server.</p>;

  const { brief, pending } = query.data;
  const running = pending?.status === "running" || write.isPending;
  const canWrite = (agent.data?.ready ?? false) && !running;
  const written = brief?.finished_at ? new Date(brief.finished_at) : null;

  return (
    <section>
      {running && (
        <p className="working">Writing a brief: reading the spec, slides and readings. This takes a minute or two</p>
      )}
      {pending?.status === "error" && <p className="problem">The brief couldn't be written: {pending.error}</p>}
      {brief ? (
        <>
          <p className="sheet-meta">
            {brief.minutes ? `About ${duration(brief.minutes)} of work, all told. ` : ""}
            {written && `Written ${shortDay(written, tz)} ${clock(written, tz)}.`}
          </p>
          <Markdown text={brief.markdown ?? ""} />
          {brief.resources.length > 0 && (
            <>
              <h3 className="group-label">Read</h3>
              <Resources resources={brief.resources} />
            </>
          )}
        </>
      ) : (
        !running && (
          <p className="quiet">
            No brief yet. A brief says what this asks, what to read first (with page numbers), a plan of attack and
            what to watch out for. It doesn't do the work for you.
          </p>
        )
      )}
      {agent.data && !agent.data.ready && <p className="quiet">{agent.data.problem}</p>}
      {canWrite && (
        <div className="sheet-actions">
          <button type="button" className={brief ? "outline-button" : "pill-button"} onClick={() => write.mutate()}>
            {brief ? "Write it again" : "Write a brief"}
          </button>
        </div>
      )}
    </section>
  );
}
