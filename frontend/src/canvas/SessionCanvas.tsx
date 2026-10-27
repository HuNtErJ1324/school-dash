import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "../api";
import { CanvasBoard, type SessionOptions } from "./CanvasBoard";

/**
 * A Tutor session's canvas, full screen, with the session's chat beside it: Help and messages from here carry
 * a snapshot, and the tutor draws its notes on it. Opening it the first time makes it.
 */
export default function SessionCanvas({ conversationId, ...session }: { conversationId: number } & SessionOptions) {
  const client = useQueryClient();
  const canvas = useQuery({
    queryKey: ["session-canvas", conversationId],
    queryFn: () => api.sessionCanvas(conversationId),
    staleTime: Infinity,
    gcTime: 0,
  });
  const made = canvas.data?.id;
  useEffect(() => {
    if (made !== undefined) void client.invalidateQueries({ queryKey: ["conversations"] }); // the list marks it
  }, [made, client]);
  return (
    <div className="session-canvas" role="dialog" aria-modal="true" aria-label={`Canvas: ${session.title}`}>
      {canvas.isPending ? (
        <p className="quiet canvas-message">Opening the canvas…</p>
      ) : canvas.isError ? (
        <p className="problem canvas-message">
          The canvas couldn't be opened: {canvas.error.message}{" "}
          <button type="button" className="text-button" onClick={session.onClose}>
            Back to the chat
          </button>
        </p>
      ) : (
        <CanvasBoard canvas={canvas.data} session={session} />
      )}
    </div>
  );
}
