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
const TITLE_END = "\n\n\n"; // a heading's or a bold label's, which a displayed equation after it doesn't continue
const BLOCKS = new Set(["paragraph", "heading", "listItem", "tableRow", "blockquote"]);
const parser = unified().use(remarkParse).use(remarkGfm).use(remarkMath);

const ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"];

/** A paragraph that is only a bold or italic label: "**Answer**". */
const isLabel = (node: Nodes) =>
  node.type === "paragraph" && node.children.every((c) => c.type === "strong" || c.type === "emphasis" || (c.type === "text" && !c.value.trim()));

function pieces(node: Nodes, out: Piece[]): void {
  switch (node.type) {
    case "text":
    case "inlineCode":
      out.push(node.value);
      return;
    case "inlineMath":
      out.push({ math: node.value });
      return;
    case "math": {
      // "Then" before a displayed equation goes on into it, rather than ending as "Then."
      const before = out.at(-2);
      if (out.at(-1) === END && typeof before === "string" && !/[.!?:]\s*$/.test(before)) out.pop();
      out.push({ math: node.value }, END);
      return;
    }
    case "list": {
      // "First, ...": a bare "5." would be a sentence of its own, and "5:" runs into "#6:". After the prose's own
      // "First, six quick questions", it's "Number 1, ..."
      const lead = out.slice(-6).filter((p): p is string => typeof p === "string").join("");
      const ordinals = !/\b(?:first|second|third)\b/i.test(lead);
      node.children.forEach((item, i) => {
        const n = (node.start ?? 1) + i;
        if (node.ordered) out.push(ordinals && n <= ORDINALS.length ? `${ORDINALS[n - 1]}, ` : `Number ${n}, `);
        pieces(item, out);
      });
      return;
    }
    case "table": // the header row is for the eye: "Time, Task" isn't a row of the plan
      for (const row of node.children.slice(1)) pieces(row, out);
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
  if (BLOCKS.has(node.type)) out.push(node.type === "heading" || isLabel(node) ? TITLE_END : END);
}

const GREEK: Record<string, string> = {
  α: "alpha", β: "beta", γ: "gamma", δ: "delta", ε: "epsilon", ϵ: "epsilon", θ: "theta", λ: "lambda", μ: "mu",
  ξ: "xi", π: "pi", σ: "sigma", φ: "phi", ω: "omega", Δ: "Delta", Σ: "Sigma", Ψ: "Psi", Φ: "Phi",
  ℝ: "the real numbers", ℕ: "the natural numbers", ℚ: "the rational numbers", ℤ: "the integers", ℂ: "the complex numbers",
  "∞": "infinity",
};  // prettier-ignore

/** Symbols and abbreviations that read better as words. */
const WORDS: [RegExp, string][] = [
  [/(?:§{1,2}|\bSections?)\s*(\d+)\.(\d+)\s*[–-]\s*(\d+)(?!\d|\.\d)/g, "sections $1.$2 to $1.$3"], // §2.1–3
  [/(?:§{1,2}|\bSections?)\s*(\d[\d.]*)\s*[–-]\s*(\d[\d.]*\d|\d)/g, "sections $1 to $2"],
  [/§§\s*/g, "sections "],
  [/§\s*/g, "section "],
  [/\bExs\.?(?=\s*\d)/g, "Examples"], // a textbook's "§2.5 Ex 4"
  [/\bEx\.?(?=\s*\d)/g, "Example"],
  [/\b(Examples?|Exercises?|Problems?)\s+(\d+)\s*\/\s*(\d+)\b/g, "$1 $2 and $3"], // "Ex 1/2" isn't one half
  [/(\d)\s*–\s*(?=\d)/g, "$1 to "], // p. 14–15, 9:00–9:50
  [/\bpp\.\s*(?=\d)/g, "pages "],
  [/\bp\.\s*(?=\d)/g, "page "],
  [/\bDef\.?(?=\s*\d)/g, "Definition"],
  [/\bThm\.?(?=\s*\d)/g, "Theorem"],
  [/\bCor\.?(?=\s*\d)/g, "Corollary"],
  [/\bProp\.?(?=\s*\d)/g, "Proposition"],
  [/\bLem\.?(?=\s*\d)/g, "Lemma"],
  [/#(?=\d)/g, "number "],
  [/\s*(⇒|⟹)\s*/g, " implies "],
  [/\s*(⇔|⟺)\s*/g, " if and only if "],
  [/\s*(→|->)\s*/g, " to "],
  [/\s*≤\s*/g, " at most "],
  [/\s*≥\s*/g, " at least "],
  [/\s*≠\s*/g, " not equal to "],
  [/\s*≈\s*/g, " about "],
  // sizes are "by": a 2×2 matrix, an 8.5×11 sheet, an m×n matrix; arithmetic is "times": 3×4 = 12, 1.5×10³
  [/(\d{1,2}(?:\.\d)?|\b[mnkpq])\s*×\s*(\d{1,2}|[mnkpq])\b(?!\s*[=+\-−*/^]|[⁰¹²³⁴⁵⁶⁷⁸⁹])/g, "$1 by $2"],
  [/\s*×\s*/g, " times "],
  // ranges that aren't numbers: parts (a)–(c), Monday–Wednesday, Q1–Q3; a name like Bolzano–Weierstrass is one name
  [/(\([a-z]\)|\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day|\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|\b[A-Z]+\d+)\s*–\s*(?=\(|[A-Z])/g, "$1 through "],
  [/\b([A-Z][a-z]+)–(?=[A-Z][a-z]+)/g, "$1-"],
  [/\b(alpha|beta|gamma|delta|epsilon|theta|lambda|mu|xi|pi|sigma|phi|omega)–(?=[A-Za-z])/g, "$1-"], // an ε–N proof
  [/\s*[—–]\s*/g, ", "],
  [/(\d)½/g, "$1 and a half"],
  [/½/g, "a half"],
  [/(\d)\s*\/\s*√/g, "$1 over √"], // 1/√2
  [/(\d)\s*√/g, "$1 times √"], // 2√2
  [/√\s*\(([^()]+)\)/g, " the square root of $1,"],
  [/√\s*(\d+|[a-zA-Z]\b)/g, " the square root of $1"],
  [/\biff\b/g, "if and only if"],
  [/\s*∈\s*/g, " in "],
  [/\s+<\s+/g, " less than "], // math typed as prose: "so that |x − a| < δ"
  [/\s+>\s+/g, " greater than "],
  [/\s+=\s+/g, " equals "],
  [/\s+−\s+/g, " minus "],
  [/²/g, " squared"],
  [/³/g, " cubed"],
  [/…/g, "..."],
  [/(^|[.:]\s+)★\s*(?=marks?\b)/g, "$1A star "], // the legend: "★ marks the key results"
  [/★\s*/g, "starred "], // "the ★ items", "★ 2.4: ..."
  [/\be\.g\.,?/g, "for example,"],
  [/\bi\.e\.,?/g, "that is,"],
  [/\bvs\.?(?=\s)/g, "versus"],
];

function tidy(block: string): string {
  let text = block
    .replace(/\s+/g, " ")
    // a space only where it meets a letter or digit: "the epsilon-version", "ε-δ" is "epsilon-delta"
    .replace(/[αβγδεϵθλμξπσφωΔΣΨΦℝℕℚℤℂ∞]/g, (c, at: number, s: string) =>
      `${/\w/.test(s[at - 1] ?? "") ? " " : ""}${GREEK[c]}${/\w/.test(s[at + 1] ?? "") ? " " : ""}`,
    )
    .replace(/[₀-₉]+/g, (digits) => ` ${[...digits].map((d) => d.charCodeAt(0) - 0x2080).join("")}`); // λ₁ is "lambda 1"
  for (const [pattern, words] of WORDS) text = text.replace(pattern, words);
  text = text
    .replace(/\s+/g, " ")
    .replace(/\s+([,.;:!?)])/g, "$1")
    .replace(/,+([,.;:!?])/g, "$1") // a formula's closing comma before the sentence's own
    .replace(/\(\s+/g, "(")
    .replace(/[,\s]+$/, "")
    .replace(/^[,\s]+/, "")
    .trim();
  return text && !/[.!?:;]$/.test(text) ? `${text}.` : text;
}

/** LaTeX with its commands as words, for when the math reader fails: \alpha + \beta is "alpha + beta". */
const plainMath = (tex: string) =>
  tex.replace(/\\([a-zA-Z]+)/g, " $1 ").replace(/'/g, " prime ").replace(/[{}\\_^&]/g, " ");

// Words the math reading follows on from, which change how it starts.
const DETERMINER = /\b(the|a|an|your|its|each|every|my|our|their)\s*$/i; // "the square root of 3 problem"
// After these the math is a noun, not a clause: "for every epsilon greater than 0", "pick capital N greater than 1 over
// epsilon", "want capital N in the natural numbers", "a natural number capital N greater than 3 over epsilon".
const QUANTIFIER =
  /\b(for|for (?:every|each|all|any|some)|all|any|some|given|with|let|there (?:is|are|exists?)|pick|choose|take|fix|find|want|include|has|number|integer|element|point|value)(?:\s+(?:an?|some))?\s*$/i; // not "you have", a statement
const RELATION = /\bis (?=(?:greater|less|at|in|not|equal)\b)/g;
// What a reading is of, as the prose may already have named it: "the set $\{x : x > 0\}$", "the matrix $A$"
const NOUN = /^the (?:(\w+ by \w+) )?((?:column |row )?vector|matrix|set|sequence|interval|sum|integral|limit|determinant)\b/;

/** How a math reading starts, given the words it follows (and what those words become). */
function joined(text: string, said: string, tex: string): [string, string] {
  const noun = NOUN.exec(said);
  const head = noun?.[2].split(" ").at(-1);
  const prose = head && new RegExp(`\\b(\\w*)${head}\\s*$`, "i").exec(text);
  if (noun && prose && !prose[1]) {
    text = text.slice(0, prose.index); // "The vector $(1, 2)$" is "The column vector 1, 2"
    said = said.slice("the ".length);
  } else if (noun && prose) said = said.slice(noun[0].length).replace(/^:? /, ""); // "the eigenvector $(1, -1)$": "1, negative 1"
  if (DETERMINER.test(text)) said = said.replace(/^the /, ""); // not "the the square root of 3"
  if (QUANTIFIER.test(text)) {
    // "Let epsilon greater than 0 be given", and the whole chain a_n \le b_n \le c_n, up to a clause of its own: "n
    // greater than N implies |s_n - s| is less than", "x squared if x is greater than 0"
    const clause = said.search(/\b(?:implies|such that|if|then|where|case)\b/);
    said = clause < 0 ? said.replace(RELATION, "") : said.slice(0, clause).replace(RELATION, "") + said.slice(clause);
  }
  // A relation on its own is a name, "try $\le$"; after "is" it continues the clause, "which is $< \varepsilon$", or
  // the question already has its verb, "Why is $a_n > \frac12$": "Why is a sub n greater than one half"
  if (/^\s*\\?(?:le|leq|ge|geq|lt|gt|ne|neq|in|subseteq|[<>=≤≥≠])\s*$/.test(tex)) said = said.replace(/^is /, "");
  else if (/\bis\s*$/.test(text)) said = /^is /.test(said) ? said.slice(3) : said.replace(/\bis (?=(?:greater|less|at|in|not|equal)\b)/, "");
  if (/^capital /.test(said)) text = text.replace(/\ban(\s*)$/i, (_, s: string) => `a${s}`); // "an $N$" is "a capital N"
  return [text, said];
}

/** A reply's markdown as words to say. */
export async function speakable(markdown: string): Promise<string> {
  const out: Piece[] = [];
  pieces(parser.parse(markdown), out);
  const maths = out.filter((p): p is { math: string } => typeof p !== "string").map((p) => p.math);
  const math = maths.length ? await import("./math") : null;
  const context = math?.reading(maths);
  let text = "";
  for (const p of out) {
    if (typeof p === "string") {
      // "$n$-th", "$S$'s", and "$n$th", which is said "n-th"
      text = /^[-'’]/.test(p) ? text.trimEnd() + p : /^(?:th|st|nd|rd)\b/.test(p) && /\S $/.test(text) ? `${text.trimEnd()}-${p}` : text + p;
      continue;
    }
    const said = await math!.mathSpeech(p.math, context).catch(() => plainMath(p.math));
    const [before, words] = joined(text, said, p.math);
    text = /-$/.test(before) ? `${before}${words} ` : `${before} ${words} `;
  }
  return text.split(/\n{2,}/).map(tidy).filter(Boolean).join(" ");
}

/**
 * Words to say in pieces, a few sentences each, so the first starts playing while the rest are made. The first
 * is short, to start soon; none is longer than `most` (the server takes 2000 characters).
 */
export function chunks(text: string, first = 160, most = 400): string[] {
  // at a sentence's end, not a colon's: "I left two items out:" and the items belong together
  const sentences = text.split(/(?<=[.!?])\s+/).flatMap((s) => split(s, most));
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
