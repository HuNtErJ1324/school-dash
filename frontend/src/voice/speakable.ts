/**
 * A tutor reply as it should sound: its words without the markdown, its math said in words (math.ts), and what
 * only makes sense on screen (diagrams, code, bare links) named rather than read out. Parsed the way the chat
 * renders it (Markdown.tsx), so $x^2$ is math and **bold** is just a word.
 */
import type { Nodes } from "mdast";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import { unified } from "unified";

type Piece = string | { math: string };

const END = "\n\n"; // where a block ends, so does its sentence
const BLOCKS = new Set(["paragraph", "heading", "listItem", "tableRow", "blockquote"]);
const parser = unified().use(remarkParse).use(remarkGfm).use(remarkMath);

function pieces(node: Nodes, out: Piece[]): void {
  switch (node.type) {
    case "text":
    case "inlineCode":
      out.push(node.value);
      return;
    case "inlineMath":
      out.push({ math: node.value });
      return;
    case "math":
      out.push({ math: node.value }, END);
      return;
    case "code":
      out.push(node.lang === "viz" ? "I drew a diagram you can play with." : "The code is on screen.", END);
      return;
    case "image":
      if (node.alt) out.push(node.alt);
      return;
    case "link": {
      const [only] = node.children;
      if (node.children.length === 1 && only.type === "text" && /^(https?:\/\/|www\.)/.test(only.value)) {
        out.push("the link on screen");
        return;
      }
      break; // its words
    }
    case "tableCell":
      for (const child of node.children) pieces(child, out);
      out.push(", ");
      return;
    case "break":
      out.push(" ");
      return;
    case "html":
    case "thematicBreak":
    case "definition":
    case "footnoteDefinition":
    case "footnoteReference":
    case "yaml":
      return;
  }
  if ("children" in node) for (const child of node.children) pieces(child as Nodes, out);
  if (BLOCKS.has(node.type)) out.push(END);
}

/** Symbols and abbreviations that read better as words. */
const WORDS: [RegExp, string][] = [
  [/\s*(→|⇒|->)\s*/g, " to "],
  [/\s*≤\s*/g, " at most "],
  [/\s*≥\s*/g, " at least "],
  [/\s*≠\s*/g, " not equal to "],
  [/\s*≈\s*/g, " about "],
  [/\s*×\s*/g, " times "],
  [/\s*[—–]\s*/g, ", "],
  [/\be\.g\.,?/g, "for example,"],
  [/\bi\.e\.,?/g, "that is,"],
  [/\bvs\.?(?=\s)/g, "versus"],
];

function tidy(block: string): string {
  let text = block.replace(/\s+/g, " ");
  for (const [pattern, words] of WORDS) text = text.replace(pattern, words);
  text = text.replace(/\s+([,.;:!?])/g, "$1").replace(/[,\s]+$/, "").replace(/^[,\s]+/, "").trim();
  return text && !/[.!?:;]$/.test(text) ? `${text}.` : text;
}

/** LaTeX with its commands as words, for when the math reader fails: \alpha + \beta is "alpha + beta". */
const plainMath = (tex: string) => tex.replace(/\\([a-zA-Z]+)/g, " $1 ").replace(/[{}\\_^]/g, " ");

/** A reply's markdown as words to say. */
export async function speakable(markdown: string): Promise<string> {
  const out: Piece[] = [];
  pieces(parser.parse(markdown), out);
  const say = out.some((p) => typeof p !== "string") ? (await import("./math")).mathSpeech : null;
  let text = "";
  for (const p of out) {
    if (typeof p === "string") text += p;
    else text += ` ${await say!(p.math).catch(() => plainMath(p.math))} `;
  }
  return text.split(/\n{2,}/).map(tidy).filter(Boolean).join(" ");
}

/**
 * Words to say in pieces, a few sentences each, so the first starts playing while the rest are made. The first
 * is short, to start soon; none is longer than `most` (the server takes 2000 characters).
 */
export function chunks(text: string, first = 160, most = 400): string[] {
  const sentences = text.split(/(?<=[.!?:;])\s+/).flatMap((s) => split(s, most));
  const out: string[] = [];
  let current = "";
  for (const s of sentences) {
    if (current && current.length + s.length + 1 > (out.length ? most : first)) {
      out.push(current);
      current = s;
    } else {
      current = current ? `${current} ${s}` : s;
    }
  }
  if (current) out.push(current);
  return out;
}

/** A sentence too long for one piece, split at its commas, or failing that its spaces. */
function split(sentence: string, most: number): string[] {
  if (sentence.length <= most) return [sentence];
  const out: string[] = [];
  let rest = sentence;
  while (rest.length > most) {
    const comma = rest.lastIndexOf(", ", most);
    const at = comma > most / 2 ? comma + 1 : rest.lastIndexOf(" ", most);
    const cut = at > most / 2 ? at : most;
    out.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) out.push(rest);
  return out;
}
