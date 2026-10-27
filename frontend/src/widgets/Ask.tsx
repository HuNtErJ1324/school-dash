import { useState } from "react";

import { Chat } from "../agent/Chat";
import { useConversations, useDeleteConversation } from "../agent/hooks";
import { TrashIcon } from "../ui/icons";

export type AskOptions = Record<string, never>;

export const askDefaults: AskOptions = {};

/** Questions for the agent, answered from your course materials, with past conversations kept. */
export function Ask(_props: { options: AskOptions }) {
  const conversations = useConversations();
  const remove = useDeleteConversation();
  const [current, setCurrent] = useState<number | null>(null);
  const list = conversations.data ?? [];

  return (
    <div className="ask">
      {list.length > 0 && (
        <div className="ask-bar">
          <select
            value={current ?? ""}
            onChange={(e) => setCurrent(e.target.value ? Number(e.target.value) : null)}
            aria-label="Conversation"
          >
            <option value="">New question</option>
            {list.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
          {current !== null && (
            <button
              type="button"
              className="icon-button"
              aria-label="Delete this conversation"
              onClick={() => remove.mutate(current, { onSuccess: () => setCurrent(null) })}
            >
              <TrashIcon />
            </button>
          )}
        </div>
      )}
      <Chat
        key={current ?? "new"}
        conversationId={current}
        onStarted={setCurrent}
        placeholder="When should I start studying for the MATH 300 midterm? Where does OSPP explain page tables?"
      />
    </div>
  );
}
