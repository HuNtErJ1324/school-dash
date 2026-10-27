import { useEffect, useState, type CSSProperties, type Dispatch, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type SetStateAction } from "react";

const KEY = "canvas-chat-size";
const NARROW = "(max-width: 760px)"; // styles.css: there the chat is a sheet over the bottom of the canvas
const DEFAULT: ChatSize = { width: 340, height: 60 };

/** The chat beside a canvas: its width in pixels, and its height as a sheet, in percent of the canvas. */
export interface ChatSize {
  width: number;
  height: number;
}

function load(): ChatSize {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (saved && typeof saved === "object" && "width" in saved && "height" in saved) {
      const { width, height } = saved as ChatSize;
      if (typeof width === "number" && typeof height === "number") return { width, height };
    }
  } catch {
    // a private window, or storage turned off: start from the default
  }
  return DEFAULT;
}

/** How big you've made the chat beside a canvas, remembered on this device. */
export function useChatSize() {
  const [size, setSize] = useState(load);
  useEffect(() => {
    const save = window.setTimeout(() => {
      try {
        localStorage.setItem(KEY, JSON.stringify(size));
      } catch {
        // not remembered, that's all
      }
    }, 300);
    return () => window.clearTimeout(save);
  }, [size]);
  const style = { "--chat-width": `${size.width}px`, "--chat-height": `${size.height}%` } as CSSProperties;
  return { size, setSize, style };
}

// Beside the canvas the chat keeps at least 260 px and leaves the canvas 240; as a sheet, 25% to 90% of it.
const width = (body: DOMRect, px: number) => Math.round(Math.min(Math.max(px, 260), Math.max(260, body.width - 240)));
const height = (percent: number) => Math.round(Math.min(Math.max(percent, 25), 90));

const KEYS: Record<string, number> = { ArrowLeft: 20, ArrowRight: -20, ArrowUp: 5, ArrowDown: -5 };

/**
 * The chat panel's edge: drag it, or focus it and use the arrow keys, to resize the chat. It's the panel's left
 * edge beside the canvas, and its top edge where the chat is a sheet over the canvas.
 */
export function ChatResizer({ size, setSize }: { size: ChatSize; setSize: Dispatch<SetStateAction<ChatSize>> }) {
  const narrow = matchMedia(NARROW).matches;
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    const body = handle.closest(".canvas-body")!.getBoundingClientRect();
    handle.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) =>
      setSize((s) =>
        narrow ? { ...s, height: height(((body.bottom - ev.clientY) / body.height) * 100) } : { ...s, width: width(body, body.right - ev.clientX) },
      );
    const done = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", done);
      handle.removeEventListener("pointercancel", done);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", done);
    handle.addEventListener("pointercancel", done);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = KEYS[e.key];
    if (!step || narrow !== (e.key === "ArrowUp" || e.key === "ArrowDown")) return;
    e.preventDefault();
    const body = e.currentTarget.closest(".canvas-body")!.getBoundingClientRect();
    setSize((s) => (narrow ? { ...s, height: height(s.height + step) } : { ...s, width: width(body, s.width + step) }));
  };
  return (
    <div
      className="canvas-chat-resizer"
      role="separator"
      aria-orientation={narrow ? "horizontal" : "vertical"}
      aria-label="Resize the chat"
      aria-valuenow={narrow ? size.height : size.width}
      aria-valuemin={narrow ? 25 : 260}
      aria-valuemax={narrow ? 90 : undefined}
      tabIndex={0}
      title="Drag to resize the chat"
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}
