import "katex/dist/katex.min.css";

import { useEffect, useRef, type RefObject } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { docHref, type Resource } from "../api";
import { BookIcon, OpenIcon } from "../ui/icons";

/** The agent cites library documents as doc:41 or doc:41#page=12; those open the document at that page. */
function transform(url: string): string {
  const doc = /^doc:(\d+)(?:#page=(\d+))?$/.exec(url);
  return doc ? docHref(Number(doc[1]), doc[2]) : defaultUrlTransform(url);
}

/** The agent's markdown, with $math$ and $$display math$$. Raw HTML in it is shown as text, never run. */
export function Markdown({ text }: { text: string }) {
  const root = useRef<HTMLDivElement>(null);
  useFitEquations(root, text);
  return (
    <div ref={root} className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        urlTransform={transform}
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" /> }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Displayed equations wrap at their = and + signs (styles.css). One that still can't fit, such as a single wide
 * fraction, shrinks until it does (down to 60%) instead of scrolling sideways, and again whenever the width
 * changes: a narrow chat panel, a resized one.
 */
function useFitEquations(root: RefObject<HTMLDivElement | null>, text: string) {
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const fit = () => {
      for (const eq of el.querySelectorAll<HTMLElement>(".katex-display")) {
        eq.style.fontSize = "";
        // A little slack: rounding would otherwise leave it a pixel too wide, and a scrollbar for that pixel.
        if (eq.scrollWidth > eq.clientWidth + 1) eq.style.fontSize = `${Math.max(0.6, (eq.clientWidth - 3) / eq.scrollWidth)}em`;
      }
    };
    let width = -1;
    const observer = new ResizeObserver(([entry]) => {
      const now = Math.round(entry.contentRect.width);
      if (now !== width) {
        width = now;
        fit();
      }
    });
    observer.observe(el);
    void document.fonts.ready.then(fit); // KaTeX's fonts change the widths once they load
    return () => observer.disconnect();
  }, [root, text]);
}

export function resourceHref(r: Resource): string | null {
  return r.doc_id !== null ? docHref(r.doc_id, r.pages) : r.url;
}

/** Links to what helps: library documents (at their pages) and web pages. */
export function Resources({ resources }: { resources: Resource[] }) {
  if (!resources.length) return null;
  return (
    <ul className="resources">
      {resources.map((r, i) => {
        const href = resourceHref(r);
        const label = r.pages ? `${r.title}, p. ${r.pages}` : r.title;
        return (
          <li key={i}>
            {r.doc_id !== null ? <BookIcon /> : <OpenIcon />}
            {href ? (
              <a href={href} target="_blank" rel="noreferrer">
                {label}
              </a>
            ) : (
              <span>{label}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** "50 min", "2 h", "1 h 30 min". */
export function duration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h} h${m ? ` ${m} min` : ""}` : `${m} min`;
}
