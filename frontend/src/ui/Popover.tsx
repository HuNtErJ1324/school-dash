import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

/**
 * A small panel anchored to a button. Rendered into <body> so a scrolling widget can't
 * clip it; flips above the button when there's no room below.
 */
export function Popover({ anchor, label, onClose, children }: {
  anchor: RefObject<HTMLElement | null>;
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    const place = () => {
      const a = anchor.current?.getBoundingClientRect();
      const el = ref.current;
      if (!a || !el) return;
      const { width, height } = el.getBoundingClientRect();
      const fitsBelow = a.bottom + 6 + height <= window.innerHeight - 8;
      setPos({
        top: fitsBelow ? a.bottom + 6 : Math.max(8, a.top - 6 - height),
        left: Math.min(Math.max(8, a.right - width), window.innerWidth - width - 8),
      });
    };
    place();
    const resize = new ResizeObserver(place); // content can change size (menu -> note editor)
    if (ref.current) resize.observe(ref.current);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      resize.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchor]);

  useEffect(() => {
    const outside = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!ref.current?.contains(target) && !anchor.current?.contains(target)) onClose();
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      onClose();
      anchor.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [anchor, onClose]);

  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("textarea, input, button, a")?.focus();
  }, []);

  return createPortal(
    <div
      ref={ref}
      className="popover"
      role="dialog"
      aria-label={label}
      style={pos ?? { top: 0, left: 0, visibility: "hidden" }}
    >
      {children}
    </div>,
    document.body,
  );
}
