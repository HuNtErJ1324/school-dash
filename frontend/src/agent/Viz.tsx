import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CloseIcon } from "../ui/icons";

/**
 * An interactive diagram from the agent: the web page in a ```viz block (the agent's AGENTS.md, "Diagrams you can
 * play with"), run in a frame of its own. The frame is sandboxed without allow-same-origin, so it can't read the
 * dashboard, its API or its storage, open windows or navigate the page, and its CSP lets it load nothing but the
 * libraries under /viz/ (vite.config.ts) and connect nowhere. It can still navigate itself away, as any frame
 * can, carrying what's in it; but what's in it is what the agent wrote, and the agent has a network of its own.
 */
export function Viz({ source }: { source: string }) {
  const doc = useMemo(() => vizDocument(source), [source]);
  const [height, setHeight] = useState(START_HEIGHT);
  const [error, setError] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  const firstError = useCallback((message: string) => setError((had) => had ?? message), []);

  return (
    <figure className="viz">
      <Frame doc={doc} height={height} onHeight={setHeight} onError={firstError} />
      <figcaption>
        {error ? <span className="problem">The diagram hit an error: {error}</span> : <span />}
        <button type="button" className="text-button" onClick={() => setFull(true)}>
          Full screen
        </button>
      </figcaption>
      {error && (
        <details>
          <summary>Its code</summary>
          <pre>
            <code>{source}</code>
          </pre>
        </details>
      )}
      {full && <FullScreen doc={doc} onError={firstError} onClose={() => setFull(false)} />}
    </figure>
  );
}

/**
 * The diagram over the whole screen, in a frame of its own (so it starts over: moving the inline one would reload
 * it anyway). A modal dialog, so it's above everything, a brief's dialog included, and Esc closes it.
 */
function FullScreen({ doc, onError, onClose }: { doc: string; onError: (message: string) => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="viz-full"
      aria-label="Interactive diagram"
      onClose={(e) => {
        e.stopPropagation(); // React passes close up its own tree: it would close a brief's dialog around this one
        onClose();
      }}
    >
      <button type="button" className="icon-button" aria-label="Close" title="Close (Esc)" onClick={() => ref.current?.close()}>
        <CloseIcon />
      </button>
      <Frame doc={doc} onError={onError} onEscape={() => ref.current?.close()} />
    </dialog>
  );
}

const START_HEIGHT = 300; // until the page says how tall it is
const MIN_HEIGHT = 32;
const MAX_HEIGHT = 1600; // styles.css also caps it at most of the screen

/**
 * The frame, and what its page tells the chat (FRAME_SCRIPT): how tall it is, what went wrong, and Esc, which
 * otherwise stays inside the frame once the student has clicked in it.
 */
function Frame({ doc, height, onHeight, onError, onEscape }: {
  doc: string;
  height?: number;
  onHeight?: (height: number) => void;
  onError: (message: string) => void;
  onEscape?: () => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    const listen = (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const said: unknown = (e.data as { viz?: unknown } | null)?.viz;
      if (!said || typeof said !== "object") return;
      const { height, error, escape } = said as { height?: unknown; error?: unknown; escape?: unknown };
      if (typeof height === "number" && Number.isFinite(height)) {
        onHeight?.(Math.round(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, height))));
      }
      if (typeof error === "string" && error) onError(error.slice(0, 300));
      if (escape === true) onEscape?.();
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, [onHeight, onError, onEscape]);
  return (
    <iframe
      ref={frame}
      title="Interactive diagram"
      sandbox="allow-scripts"
      srcDoc={doc}
      referrerPolicy="no-referrer"
      loading="lazy"
      style={height === undefined ? undefined : { height }}
    />
  );
}

// The dashboard's palette (styles.css), handed to the page as the same CSS variables.
const PALETTE = ["--bg", "--bg-raised", "--bg-inset", "--text", "--subtle", "--muted", "--border", "--border-bright",
  "--rose", "--gold", "--iris", "--foam", "--pine", "--love"];  // prettier-ignore

/**
 * Runs first in every diagram: tells the chat the page's height as it changes, any error (a script that throws
 * or fails to load, something the CSP blocked), so a broken diagram says why instead of sitting blank, and Esc.
 */
const FRAME_SCRIPT = `(() => {
  const post = (viz) => parent.postMessage({ viz }, "*");
  let height = -1;
  const measure = () => {
    const h = Math.ceil(Math.max(document.documentElement.getBoundingClientRect().height, document.body ? document.body.scrollHeight : 0));
    if (h !== height) post({ height: (height = h) });
  };
  const sizes = new ResizeObserver(measure);
  sizes.observe(document.documentElement);
  addEventListener("DOMContentLoaded", () => sizes.observe(document.body));
  addEventListener("load", measure);
  addEventListener("error", (e) => {
    const failed = e.target instanceof Element ? e.target.src || e.target.href : null;
    post({ error: failed ? "couldn't load " + failed : e.message || "a script failed" });
  }, true);
  addEventListener("unhandledrejection", (e) => post({ error: String((e.reason && e.reason.message) || e.reason) }));
  addEventListener("securitypolicyviolation", (e) =>
    post({ error: "blocked " + (e.blockedURI || e.violatedDirective) + " (a diagram can load only the libraries under /viz/)" }));
  addEventListener("keydown", (e) => e.key === "Escape" && post({ escape: true }));
})();`;

/** The page the frame runs: the agent's HTML, after the frame's rules, its palette and FRAME_SCRIPT. */
export function vizDocument(source: string): string {
  const lib = new URL("/viz/", window.location.href).href;
  const csp = [
    "default-src 'none'",
    // The page is the agent's code to begin with, so eval adds nothing it couldn't do (and p5 needs it).
    `script-src 'unsafe-inline' 'unsafe-eval' ${lib}`,
    `style-src 'unsafe-inline' ${lib}`,
    `font-src ${lib}`,
    "img-src data: blob:",
    "media-src data: blob:",
  ].join("; ");
  const imports = { imports: { three: `${lib}three/three.module.js`, "three/addons/": `${lib}three/addons/` } };
  const css = getComputedStyle(document.documentElement);
  const palette = PALETTE.map((name) => `${name}: ${css.getPropertyValue(name).trim()};`).join(" ");
  return `<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<script type="importmap">${JSON.stringify(imports)}</script>
<script>${FRAME_SCRIPT}</script>
<style>
@font-face { font-family: "CommitMono"; src: url("${lib}fonts/CommitMonoNF-Regular.woff2") format("woff2"); font-weight: 400; }
@font-face { font-family: "CommitMono"; src: url("${lib}fonts/CommitMonoNF-Bold.woff2") format("woff2"); font-weight: 700; }
:root { color-scheme: dark; ${palette} }
html, body { margin: 0; }
body { padding: 10px; background: var(--bg-raised); color: var(--text); font: 13px/1.5 "CommitMono", ui-monospace, monospace; }
button, input, select, textarea { font: inherit; color: inherit; }
button { padding: 3px 10px; background: var(--bg-inset); border: 1px solid var(--border-bright); cursor: pointer; }
button:hover:not(:disabled) { border-color: var(--rose); color: var(--rose); }
button:disabled { opacity: 0.5; cursor: default; }
input, select, textarea { background: var(--bg-inset); border: 1px solid var(--border); }
input[type="range"], input[type="checkbox"], input[type="radio"] { accent-color: var(--foam); }
svg { max-width: 100%; }
</style>
</head><body>
${source}
</body></html>`;
}
