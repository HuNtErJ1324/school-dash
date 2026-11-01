/**
 * Math read aloud: KaTeX turns the LaTeX into MathML, and Speech Rule Engine (what screen readers use) says it the
 * ClearSpeak way: "the limit as n goes to infinity of a sub n", "x squared plus 3 x". Loaded only when a reply with
 * math is read, since its rules are half a megabyte. They're served from the dashboard, not SRE's CDN.
 *
 * ClearSpeak alone reads a lot of tutoring math badly ("s sub n right arrow s", "sup of S", "the absolute value of
 * times a times..."), so each expression is fixed in three places: the LaTeX before KaTeX (operators and arrows said
 * as words), KaTeX's MathML before SRE (bars paired, fractions grouped, accents and primes it can name), and SRE's
 * words after.
 */
import katex from "katex";
import SRE from "speech-rule-engine/lib/sre.js";
import baseUrl from "speech-rule-engine/lib/mathmaps/base.json?url";
import enUrl from "speech-rule-engine/lib/mathmaps/en.json?url";

const MAPS: Record<string, string> = { base: baseUrl, en: enUrl };

// ClearSpeak's preferences: "n is in N" (not "is a member of"), "the set of x such that" (not "of all x"),
// "natural log", "Delta x" (not "triangle x"), "end matrix" after a matrix, and no "2 lines" before aligned rows.
const STYLE = "SetMemberSymbol_In:Sets_woAll:Log_LnAsNaturalLog:TriangleSymbol_Delta:Matrix_EndVector:MultiLineOverview_None";

const ready = (async () => {
  await SRE.setupEngine({
    locale: "en",
    domain: "clearspeak",
    modality: "speech",
    style: STYLE,
    custom: async (locale: string) => {
      const res = await fetch(MAPS[locale] ?? MAPS.en);
      if (!res.ok) throw new Error(`speech rules for ${locale}: HTTP ${res.status}`);
      return res.text();
    },
  });
  await SRE.engineReady();
})();

const T = String.raw;

/* ---------------------------------------------------------------- the LaTeX: what to say in words before KaTeX */

/** The TeX group starting at i (after spaces): {...} with nesting, a \command, or one character. */
function group(tex: string, i: number): { body: string; end: number } | null {
  while (tex[i] === " ") i++;
  if (tex[i] === "{") {
    let depth = 0;
    for (let j = i; j < tex.length; j++) {
      if (tex[j] === "\\") {
        j++;
        continue;
      }
      if (tex[j] === "{") depth++;
      else if (tex[j] === "}" && --depth === 0) return { body: tex.slice(i + 1, j), end: j + 1 };
    }
    return null;
  }
  const cmd = /^\\([a-zA-Z]+|.)/.exec(tex.slice(i));
  if (cmd) return { body: cmd[0], end: i + cmd[0].length };
  return i < tex.length ? { body: tex[i], end: i + 1 } : null;
}

const OPERATORS: Record<string, string> = {
  limsup: "limit superior", liminf: "limit inferior", lim: "limit",
  sup: "supremum", inf: "infimum", max: "maximum", min: "minimum", det: "determinant", dim: "dimension", ker: "kernel",
};  // prettier-ignore

/**
 * \sup S, \lim s_n, \max\{a,b\}, \sup_{x\in S} f(x), \lim_{x\to a^+}: "the supremum of S", "the limit as x goes to
 * a from the right of", whatever follows. A bare \det, with nothing after it, is just "the determinant".
 */
function operators(tex: string): string {
  const re = /\\(limsup|liminf|lim|sup|inf|max|min|det|dim|ker)(?![a-zA-Z])\s*(\\(?:no)?limits)?/g;
  let out = "";
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(tex))) {
    const name = OPERATORS[m[1]];
    let end = re.lastIndex;
    let said = /^\s*($|[,;.=<>&]|\\\\|\\(?:text|quad|qquad)\b)/.test(tex.slice(end)) ? T`\text{the ${name}}` : T`\text{the ${name} of }`;
    let j = end;
    while (tex[j] === " ") j++;
    if (tex[j] === "_") {
      const sub = group(tex, j + 1);
      if (!sub) continue;
      const under = sub.body
        .replace(/\^\s*\{?\s*\+\s*\}?/g, T`\text{ from the right}`)
        .replace(/\^\s*\{?\s*-\s*\}?/g, T`\text{ from the left}`)
        .replace(/\\(?:to|rightarrow)(?![a-zA-Z])/g, T`\text{ goes to }`);
      said = T`\text{the ${name} ${m[1].startsWith("lim") ? "as" : "over"} }{${under}}\text{ of }`;
      end = sub.end;
    }
    out += tex.slice(last, m.index) + said;
    last = end;
    re.lastIndex = end;
  }
  return out + tex.slice(last);
}

/**
 * f\colon A\to B is "f from A to B"; any other \colon is a plain colon (KaTeX's \colon reads as "comma colon"). In a
 * set, \{t \colon s_n \to t\}, it means "such that", which a plain colon says.
 */
function colon(tex: string): string {
  const inSet = (at: number) => {
    const before = tex.slice(0, at);
    return (before.match(/\\\{/g)?.length ?? 0) > (before.match(/\\\}/g)?.length ?? 0);
  };
  return tex
    .replace(/\\colon\s*((?:(?!\\colon)[^])*?)\\(?:to|rightarrow|longrightarrow)(?![a-zA-Z])/g, (all, between: string, at: number) =>
      inSet(at) ? all : T`\text{ from }${between}\text{ to }`,
    )
    .replace(/^(\s*[a-zA-Z])\s*:\s*((?:(?!\\to)[^])+?)\\to(?![a-zA-Z])/, T`$1\text{ from }$2\text{ to }`) // f: A \to B, typed plainly
    .replace(/\\colon(?![a-zA-Z])/g, ":");
}

/** Arrows and logic said the way they're meant: a sequence a_n \to L converges, x \to a and s_n \to \infty go. */
function words(tex: string): string {
  if (/\\(?:b|p)?mod(?![a-zA-Z])|\\text\{ mod \}/.test(tex)) tex = tex.replace(/\\equiv(?![a-zA-Z])/g, T`\text{ is congruent to }`);
  return tex
    .replace(/\\(?:bmod|mod)(?![a-zA-Z])/g, T`\text{ mod }`) // KaTeX's \bmod is a word SRE can't read
    .replace(/\\lfloor\s*((?:(?!\\rfloor)[^])+?)\s*\\rfloor/g, T`{\text{the floor of }{$1}}`) // SRE drops ⌊x⌋ around one letter
    .replace(/\\lceil\s*((?:(?!\\rceil)[^])+?)\s*\\rceil/g, T`{\text{the ceiling of }{$1}}`)
    .replace(/\\(?:not\s*\\(?:subseteq|subset)|nsubseteq|nsubset)(?![a-zA-Z])/g, T`\text{ is not a subset of }`)
    .replace(/\\(?:not\s*\\(?:supseteq|supset)|nsupseteq|nsupset)(?![a-zA-Z])/g, T`\text{ does not contain }`)
    .replace(/(?<![\\a-zA-Z])O\s*\(([^()]+)\)/g, T`\text{big O of }{$1}`) // O(n^2), not "O times open paren"
    .replace(/\\(?:subsetneq|subsetneqq|varsubsetneq)(?![a-zA-Z])/g, T`\text{ is a proper subset of }`)
    .replace(/\\(?:supsetneq|supsetneqq)(?![a-zA-Z])/g, T`\text{ properly contains }`)
    .replace(/\\(?:nearrow|uparrow)(?![a-zA-Z])/g, T`\text{ increases to }`) // s_n \nearrow s
    .replace(/\\(?:searrow|downarrow)(?![a-zA-Z])/g, T`\text{ decreases to }`)
    .replace(/\\(?:iff|Longleftrightarrow|Leftrightarrow)(?![a-zA-Z])/g, T`\text{ if and only if }`)
    .replace(/\\(?:implies|Longrightarrow|Rightarrow)(?![a-zA-Z])/g, T`\text{ implies }`)
    .replace(/\\(?:impliedby|Longleftarrow|Leftarrow)(?![a-zA-Z])/g, T`\text{ is implied by }`)
    .replace(/\\(?:mapsto|longmapsto)(?![a-zA-Z])/g, T`\text{ maps to }`)
    // x \to a^+ is "x goes to a from the right", not "a raised to the plus power"
    .replace(/(\\(?:to|rightarrow)\s*(?:\d+|[a-zA-Z]))\s*\^\s*\{?\s*\+\s*\}?/g, T`$1\text{ from the right}`)
    .replace(/(\\(?:to|rightarrow)\s*(?:\d+|[a-zA-Z]))\s*\^\s*\{?\s*-\s*\}?/g, T`$1\text{ from the left}`)
    // a sequence converges: a_n \to L, |a_n| \to |L|, (b_n)^n \to 0; anything else goes: x \to a, s_n \to \infty
    .replace(/((?:_\s*(?:\{[^{}]*\}|[a-zA-Z0-9])|\)\s*\^\s*(?:\{[^{}]*\}|[a-zA-Z0-9]))\s*[)|]?\s*)\\(?:to|rightarrow|longrightarrow)(?![a-zA-Z])(?!\s*[+-]?\s*\\infty)/g, T`$1\text{ converges to }`)
    .replace(/\\(?:to|rightarrow|longrightarrow)(?![a-zA-Z])/g, T`\text{ goes to }`)
    .replace(/\\forall(?![a-zA-Z])/g, T`\text{for every }`)
    .replace(/\\nexists(?![a-zA-Z])/g, T`\text{there is no }`)
    .replace(/\\exists\s*!/g, T`\text{there is a unique }`)
    .replace(/\\exists(?![a-zA-Z])/g, T`\text{there is }`)
    .replace(/\\(?:subseteq|subset)(?![a-zA-Z])/g, T`\text{ is a subset of }`)
    .replace(/\\(?:supseteq|supset)(?![a-zA-Z])/g, T`\text{ contains }`)
    .replace(/\\sim(?![a-zA-Z])/g, T`\text{ is asymptotic to }`)
    // vectors: \mathbf{u}\times\mathbf{v} is "u cross v", \mathbf{u}\cdot\mathbf{v} "u dot v"
    .replace(/(\\(?:mathbf|vec|boldsymbol)\s*\{?\s*\\?[a-zA-Z]+\s*\}?)\s*\\times(?![a-zA-Z])/g, T`$1\text{ cross }`)
    .replace(/(\\(?:mathbf|vec|boldsymbol)\s*\{?\s*\\?[a-zA-Z]+\s*\}?)\s*\\cdot(?=\s*\\(?:mathbf|vec|boldsymbol))/g, T`$1\text{ dot }`)
    // sizes are "by": 2 \times 2, m \times n, \mathbb{R}^{n \times n}; arithmetic is "times": 3 \times 4 = 12, 1 \times 4 - 1
    .replace(/(?<![=+\-]\s*)(?<![\\a-zA-Z\d.])([mnkpq]|\d{1,2})\s*\\times\s*([mnkpq]|\d{1,2})(?![a-zA-Z\d.])(?!\s*(?:=|\+|-|\\cdot|\\times|\^))/g, T`$1\text{ by }$2`);
}

/** \text{dist}(a,b) is "the distance from a to b"; any other \text{name}( is a function, as \operatorname is. */
function functions(tex: string): string {
  const NAMED: Record<string, string> = { Re: "the real part of", Im: "the imaginary part of" };
  return tex
    .replace(/\\(?:text|operatorname|mathrm)\{dist\}\s*\(([^(),]+),([^()]+)\)/g, T`\text{the distance from }{$1}\text{ to }{$2}`)
    .replace(/\\(?:operatorname|text|mathrm)\{(Re|Im)\}/g, (_, f: string) => T`\text{${NAMED[f]} }`)
    .replace(/\\text\{([a-zA-Z]{2,})\}(?=\s*\()/g, T`\operatorname{$1}`);
}

/** Where the base of a script ending just before index i starts: a (...) or [...] group, a {...} group, or one token. */
function baseStart(tex: string, i: number): { at: number; group: boolean } | null {
  const base = tokenStart(tex, i);
  // \boldsymbol\xi^{(1)}: the base is \boldsymbol\xi, or the label lands inside the bold, which KaTeX can't render
  const style = base && /\\(?:boldsymbol|mathbf|bm|vec|hat|bar|tilde|overline|mathrm|mathcal)\s*$/.exec(tex.slice(0, base.at));
  return base && style ? { at: style.index, group: false } : base;
}

function tokenStart(tex: string, i: number): { at: number; group: boolean } | null {
  let j = i;
  while (j > 0 && tex[j - 1] === " ") j--;
  const env = /\\end\{([a-zA-Z*]+)\}$/.exec(tex.slice(0, j)); // \begin{pmatrix}...\end{pmatrix}^{-1}
  if (env) {
    const at = tex.lastIndexOf(T`\begin{${env[1]}}`, env.index);
    return at < 0 ? null : { at, group: true };
  }
  const close = tex[j - 1];
  const open = ({ ")": "(", "]": "[", "}": "{" } as Record<string, string>)[close];
  if (open) {
    let depth = 0;
    for (let k = j - 1; k >= 0; k--) {
      if (tex[k] === close && tex[k - 1] !== "\\") depth++;
      else if (tex[k] === open && tex[k - 1] !== "\\" && --depth === 0) {
        if (open === "{") return { at: k, group: false };
        // \Psi(0)^{-1}, f(x)^2: the function and its argument are the base, said "Psi of 0 inverse"
        const fn = /(\\[a-zA-Z]+|\\mathbf\{[a-zA-Z]\}|[a-zA-Z])\s*$/.exec(tex.slice(0, k));
        return fn ? { at: fn.index, group: false } : { at: k, group: true };
      }
    }
    return null;
  }
  const cmd = /\\[a-zA-Z]+$/.exec(tex.slice(0, j));
  return { at: cmd ? cmd.index : j - 1, group: false };
}

/**
 * Superscripts that aren't powers: x^{(1)} is a label, A^T a transpose, A^* a star, M^{-1} an inverse. A bracketed
 * base comes after the words ("the transpose of (AB)"): SRE drops the parentheses of (AB) when words follow it. Each
 * is grouped, so A^{-1} - B^{-1} is "A inverse minus B inverse", not "A inverse negative B inverse".
 */
const SUPERSCRIPTS: Record<string, string> = { "-1": "inverse", "*": "star", [T`\ast`]: "star", c: "complement", [T`\complement`]: "complement" };

function superscripts(tex: string): string {
  // ^T takes the one token T, so A^TA is A^T A
  const re = /\^\s*(?:\{\s*(T|\\top|\\intercal|\\mathsf\s*\{?T\}?|\*|\\ast|-\s*1|c|\\complement|\(\s*[^(){}]+?\s*\))\s*\}|(T|\*|c(?![a-zA-Z]))|(\\top|\\intercal|\\ast|\\complement)(?![a-zA-Z]))/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(tex))) {
    const what = (m[1] ?? m[2] ?? m[3]).replace(/\s+/g, "");
    const label = /^\((.+)\)$/.exec(what)?.[1]; // x^{(1)}, the first solution: "x superscript 1"
    const word = label ? `superscript ${label}` : (SUPERSCRIPTS[what] ?? "transpose");
    const base = baseStart(tex, m.index);
    if (!base) continue;
    const baseTex = tex.slice(base.at, m.index).trim();
    if (word === "complement" && !/^[A-Z]$/.test(baseTex)) continue; // A^c, a set; x^c is a power
    // \int_0^T and \sum_{t=1}^T: an upper limit, not a transpose; 2^T and e^T are powers
    if (!label && (/_\s*$/.test(tex.slice(0, base.at)) || /^(?:\d|e$)/.test(baseTex))) continue;
    // An inverse only of matrix-like things (capitals, matrices, brackets with a capital in): (n+1)^{-1} and 2^{-1}
    // are powers, and SRE says f^{-1}(x) well.
    const matrixLike = /^(?:[A-Z]|\\(?:Psi|Phi|Lambda|Sigma|Gamma|Omega|Theta|Delta|mathbf)|\\begin)/.test(baseTex) || /^[([].*[A-Z]/.test(baseTex);
    if (word === "inverse" && (!matrixLike || /^\s*\(/.test(tex.slice(re.lastIndex)))) continue;
    let said = /^[([]|\\begin/.test(baseTex) && !label ? T`{\text{the ${word} of }${baseTex}}` : T`{${baseTex}\text{ ${word}}}`;
    let end = re.lastIndex;
    const argument = label ? /^\s*\(([^()]{1,12})\)/.exec(tex.slice(end)) : null; // x^{(1)}(t): "x superscript 1 of t"
    if (argument) {
      said += T`\text{ of }{${argument[1]}}`;
      end += argument[0].length;
    }
    tex = tex.slice(0, base.at) + said + tex.slice(end);
    re.lastIndex = base.at + said.length;
  }
  return tex;
}

/** The LaTeX with what ClearSpeak says badly put in words, the way a teacher would say it. */
export function rewrite(tex: string): string {
  tex = tex
    .replace(/\\([RNZQC])(?![a-zA-Z])/g, T`\mathbb{$1}`) // the \R shorthand, before the rules below look for \mathbb
    .replace(/(?<!\\(?:right|left|big|Big|bigg|Bigg)?)(?<!\\)\s*[.,;]\s*$/, "") // the sentence's own full stop, typed in the math
    .replace(/\\(begin|end)\{(?:align|alignat|eqnarray|flalign)\*?\}/g, T`\$1{aligned}`) // KaTeX takes these only displayed
    .replace(/\\(begin|end)\{(?:gather|multline)\*?\}/g, T`\$1{gathered}`)
    .replace(/\\(?:begin|end)\{equation\*?\}/g, "")
    // (s_n)_{n=1}^\infty, the sequence: "s sub n, for n from 1 to infinity"
    .replace(/(?:\(|\\\{)\s*((?:[^(){}]|\{[^{}]*\})+?)\s*(?:\)|\\\})\s*_\s*\{\s*([a-z])\s*=\s*([^{}]+?)\s*\}\s*\^\s*\{?\s*\\infty\s*\}?/g, T`{$1}\text{, for }$2\text{ from }{$3}\text{ to infinity}`)
    // (a_n)_{n\in\mathbb{N}}, \{a_n\}_{n\ge 1}: "a sub n, for n in the natural numbers"; a bare \{a_n\} is the sequence too
    .replace(/(?:\(|\\\{)\s*([a-zA-Z]_(?:\{[^{}]*\}|[a-zA-Z0-9]))\s*(?:\)|\\\})\s*_\s*\{((?:[^{}]|\{[^{}]*\})+)\}/g, T`{$1}\text{, for }{$2}`)
    .replace(/\\\{\s*([a-zA-Z]_(?:\{[^{}]*\}|[a-zA-Z0-9]))\s*\\\}/g, "{$1}")
    .replace(/\\binom\{([^{}]*)\}\{([^{}]*)\}/g, T`{$1}\text{ choose }{$2}`)
    .replace(/\\pmod\{([^{}]*)\}/g, T`\text{ mod }{$1}`)
    .replace(/\\mathbb\{([A-Z])\}\s*\^\s*(\{[^{}]*\}|\w)/g, T`\mathrm{$1}\,$2`) // ℝ^n is "R n", not "r-n" or "R to the n"
    .replace(/(\\(?:det|dim)\s*(?:[A-Z]|\\[a-zA-Z]+))\s*(?=\\(?:det|dim)(?![a-zA-Z]))/g, T`$1 \cdot `); // \det A \det B: "times"
  return [operators, colon, words, functions, superscripts].reduce((t, fix) => fix(t), tex);
}

/* ------------------------------------------------------------------- the MathML: what SRE misreads from KaTeX */

const MATHML = "http://www.w3.org/1998/Math/MathML";
const els = (n: Node): Element[] => Array.from(n.childNodes).filter((c): c is Element => c.nodeType === 1);
const text = (n: Node) => (n.textContent ?? "").trim();
const ROWS = new Set(["math", "mrow", "mstyle", "mtd", "msqrt", "menclose", "mpadded", "mphantom"]);
const SCRIPTS = new Set(["msub", "msup", "msubsup"]);
const BARS: Record<string, string> = { "∣": "abs", "|": "abs", "∥": "norm", "‖": "norm" };
const CLOSERS = new Set([")", "]", "}", "⟩", "⌋", "⌉", "!", "′", "″", "‴", "%", "°"]);
const INVISIBLE = /[\u2061-\u2064]/; // function application, invisible times, separator, plus
const BLANK = /^[\s\u200b\u2061-\u2064]*$/;
const VECTOR_ARROW = "\u20d7"; // \vec's combining arrow
const PRIMES: Record<number, string> = { 2: "″", 3: "‴", 4: "⁗" };

/** What kind of bar a node is: | and \vert and \lvert and \big| and \left| are; \mid, \parallel, \middle| are not. */
function barKind(n: Element | undefined): string | null {
  if (!n || (n.tagName !== "mi" && n.tagName !== "mo")) return null;
  const kind = BARS[text(n)];
  if (!kind || (n.tagName === "mo" && n.attributes.length === 0) || n.getAttribute("lspace")) return null;
  return kind;
}
/** A closing bar can carry the script: KaTeX puts |x|^2's ^2 on the last bar alone. */
const scriptedBar = (n: Element) => (SCRIPTS.has(n.tagName) ? barKind(els(n)[0]) : null);
const isSpace = (n: Element) => n.tagName === "mspace" || (n.tagName === "mtext" && BLANK.test(n.textContent ?? ""));
/** Can a bar after this node close an absolute value? After an operand yes; after an operator, a word or nothing, no. */
const endsOperand = (n: Element) => (n.tagName === "mo" ? CLOSERS.has(text(n)) : n.tagName !== "mtext");

/**
 * Pair the bars of one row, innermost first, and give each pair its own mrow, the way \left|...\right| does. SRE
 * pairs neighbouring bars in a flat row, so \big||a|-|b|\big| came out as "the absolute value of times a times...".
 */
function pairBars(row: Element, doc: Document): Element[] {
  const out: Element[] = [];
  const open: { at: number; kind: string }[] = [];
  const pairs: Element[] = [];
  for (const child of els(row)) {
    const kind = barKind(child) ?? scriptedBar(child);
    if (kind) {
      let p = out.length - 1;
      while (p >= 0 && isSpace(out[p])) p--;
      const top = open.at(-1);
      if (top && top.kind === kind && p > top.at && endsOperand(out[p])) {
        const pair = doc.createElementNS(MATHML, "mrow");
        for (const n of out.splice(top.at)) pair.appendChild(n);
        pairs.push(pair);
        open.pop();
        if (SCRIPTS.has(child.tagName)) {
          const bar = els(child)[0];
          child.replaceChild(pair, bar);
          pair.appendChild(bar);
          out.push(child);
        } else {
          pair.appendChild(child);
          out.push(pair);
        }
        continue;
      }
      if (!SCRIPTS.has(child.tagName)) open.push({ at: out.length, kind });
    }
    out.push(child);
  }
  const before = els(row);
  if (out.length === before.length && out.every((n, i) => n === before[i])) return pairs;
  for (const n of before) row.removeChild(n);
  for (const n of out) row.appendChild(n);
  return pairs; // innermost first
}

/** A cell's formula, without KaTeX's <mstyle><mrow> wrapper. */
function contents(cell: Element): Element[] {
  let nodes = els(cell);
  while (nodes.length === 1 && (nodes[0].tagName === "mstyle" || nodes[0].tagName === "mrow")) nodes = els(nodes[0]);
  return nodes;
}

function word(doc: Document, words: string): Element {
  const mtext = doc.createElementNS(MATHML, "mtext");
  mtext.appendChild(doc.createTextNode(words));
  return mtext;
}

/** A sum or difference: 4n + 2, not -1 or 2n. */
const isSum = (n: Element) => contents(n).some((k, i) => i > 0 && k.tagName === "mo" && /^[+\-−±∓]$/.test(text(k)));
/** |x|, which pairBars grouped. */
const isBarPair = (n: Element) => n.tagName === "mrow" && els(n).length >= 3 && !!barKind(els(n)[0]) && !!barKind(els(n).at(-1));
/** a_{n+1}: ClearSpeak says "a sub n plus 1" with nothing to tell it from a_n + 1. Not \sum_{k=n+1}, an operator. */
const compoundScript = (n: Element) =>
  (n.tagName === "msub" || n.tagName === "msubsup") && els(n)[0]?.tagName === "mi" && !!els(n)[1] && isSum(els(n)[1]);

/**
 * What a fraction's top or bottom needs to be heard as one thing: a sum or a fraction already said in words (a/b over
 * c) need "the quantity"; an absolute value (|x| over x) and a_{n+1} start clearly and only need their end marked.
 */
function grouping(n: Element): "quantity" | "bars" | "marked" | null {
  for (let e: Element | undefined = n; e; e = els(e).length === 1 ? els(e)[0] : undefined) {
    if (e.tagName === "mfrac" || e.getAttribute("data-said") === "fraction") return "quantity"; // a over b/c
  }
  const nodes = contents(n);
  if (nodes.length > 1 && barKind(nodes[0]) && barKind(nodes.at(-1))) return "bars";
  if (compoundScript(n) || (nodes.length === 1 && compoundScript(nodes[0]))) return "marked";
  return isSum(n) ? "quantity" : null;
}

/**
 * Where an expression ends, said as a pause when something follows: a_{n+1}, |a - b|, \sqrt{n+1}, and a fraction
 * before + or -, so "1 over 4 epsilon, plus 2" isn't heard as 1/(4ε + 2).
 */
function needsEnd(wrapped: Element, next: Element | undefined, after: Element | undefined, inBars: boolean): boolean {
  if (!next || /^[)\]}]$/.test(text(next)) || (next.tagName === "mtext" && /^\s*,/.test(text(next)))) return false;
  let n = wrapped; // \tfrac is an <mfrac> inside an <mstyle>
  while (els(n).length === 1 && (n.tagName === "mstyle" || n.tagName === "mrow") && !n.getAttribute("data-said")) n = els(n)[0];
  const plusOrMinus = next.tagName === "mo" && /^[+\-−]$/.test(text(next));
  // |a + b| and, before + or -, any |a|: "|a|, plus |b|" isn't heard as |a + |b||
  if (compoundScript(n) || (isBarPair(n) && (isSum(n) || plusOrMinus))) return true;
  if ((n.tagName === "msqrt" && isSum(n)) || (n.tagName === "mroot" && els(n)[0] && isSum(els(n)[0]))) return true;
  // a fraction said in words, before + or -: "1 over 4 epsilon, plus 2" ("one half plus epsilon" is clear as it is)
  if (n.getAttribute("data-said") === "fraction" && text(els(n).at(-1)!) !== ",") return plusOrMinus;
  // a_n + 1 is "a sub n, plus 1", since "a sub n plus 1" is a_{n+1}; but not inside |s_n - 1|, where the pause would
  // sound like the bar closing, nor before a letter (s_n + t_n), which a subscript can't take in
  return !inBars && plusOrMinus && n.tagName === "msub" && els(n)[0]?.tagName === "mi" && after?.tagName === "mn";
}

/**
 * A matrix in parentheses or brackets, said entry by entry: "the 2 by 2 matrix: row 1: 1, negative 1, row 2: 1, 3,
 * end matrix". ClearSpeak runs the entries together ("Row 1: 1 negative 1"), or, given anything between them, labels
 * every column.
 */
function matrix(open: Element, table: Element, close: Element, doc: Document): Element | null {
  const rows = els(table).filter((r) => r.tagName === "mtr").map((r) => els(r).filter((c) => c.tagName === "mtd"));
  const columns = Math.max(...rows.map((r) => r.length));
  const determinant = text(open) === "|" || text(open) === "∣"; // vmatrix
  if (!rows.length || !columns || !(/^[([]$/.test(text(open)) || determinant) || !/^[)\]|∣]$/.test(text(close))) return null;
  const said = doc.createElementNS(MATHML, "mrow");
  said.setAttribute("data-said", "matrix");
  const vector = !determinant && (rows.length === 1 || columns === 1);
  const general = /[⋯⋮⋱…]/.test(text(table)); // a_{11} & \cdots & a_{1n}: an m by n matrix, not "3 by 3"
  const size = general ? "" : `${rows.length} by ${columns} `;
  said.appendChild(word(doc, `${determinant ? "the determinant of " : ""}${vector ? `the ${columns === 1 ? "column" : "row"} vector ` : `the ${size}matrix: `}`));
  rows.forEach((cells, r) => {
    if (!vector) said.appendChild(word(doc, `${r ? ", " : ""}row ${r + 1}: `));
    cells.forEach((cell, c) => {
      if (c || (vector && r)) said.appendChild(word(doc, ", "));
      for (const x of contents(cell)) said.appendChild(x);
    });
  });
  said.appendChild(word(doc, vector ? ", end vector" : determinant ? ", end determinant" : ", end matrix"));
  return said;
}

/**
 * A fraction said the way a teacher says it: "1 over the quantity 4 n plus 2", "1 plus x, all over the quantity 2
 * plus x", rather than ClearSpeak's "the fraction with numerator 1 and denominator 4 n plus 2". Single symbols over
 * single symbols ("one half", "3 over n") are left to ClearSpeak, except over 1, which it calls "two oneths".
 */
function fraction(n: Element, doc: Document): Element | null {
  const [top, bottom] = els(n);
  const single = (k: Element) => ["mi", "mn"].includes(k.tagName);
  if (!top || !bottom || (single(top) && single(bottom) && text(bottom) !== "1")) return null;
  const [above, below] = [grouping(top), grouping(bottom)];
  const row = doc.createElementNS(MATHML, "mrow");
  row.setAttribute("data-said", "fraction");
  row.appendChild(top);
  if (above) row.appendChild(word(doc, ", all over "));
  else if (below === "bars") {
    // a lone "over" right before |x - a| is taken for a function ("the over of the absolute value"); ÷ is "over"
    const divide = doc.createElementNS(MATHML, "mo");
    divide.appendChild(doc.createTextNode("÷"));
    row.appendChild(divide);
  } else row.appendChild(word(doc, " over "));
  if (below === "quantity") row.appendChild(word(doc, "the quantity "));
  row.appendChild(bottom);
  if (above || below) row.appendChild(word(doc, ",")); // where it ends
  return row;
}

const FUNCTIONS = /^(?:sin|cos|tan|sec|csc|cot|sinh|cosh|tanh|ln|log|exp)$/;

/**
 * Where "times" is heard though not written: s_n t_n, \Psi(t)\,\mathbf{c}, f(x)g(x), |x-2|\,|x+2|, x\sin x. Not
 * s_n/t_n, \sum_k a_k, f(x)\,dx.
 */
function impliedTimes(row: Element[], i: number): boolean {
  const here = row[i];
  const said = (e: Element | undefined) => {
    while (e && e.tagName === "mrow" && els(e).length === 1) e = els(e)[0]; // a matrix in brackets is a row of its own
    while (e?.getAttribute("data-said") === "ended") e = els(e)[0];
    return e?.getAttribute("data-said") ?? null;
  };
  // "1 over the quantity a d minus b c, times the 2 by 2 matrix", "1 over n, times the sum"
  const isFraction = said(here) === "fraction" || here.tagName === "mfrac" || els(here)[0]?.tagName === "mfrac";
  if (isFraction && row[i + 1] && (said(row[i + 1]) === "matrix" || /^[∑∫]/.test(text(row[i + 1])))) return true;
  const closes = (here.tagName === "mo" && text(here) === ")") || said(here) === "matrix"; // "end matrix, times x"
  const bars = isBarPair(here);
  let k = i + 1;
  if (closes || bars) while (row[k] && isSpace(row[k])) k++; // \Psi(t)\,\mathbf{g}(t), |x-2|\,|x+2|
  const ended = row[k]?.getAttribute("data-said") === "ended"; // y_1 with its pause after it, in x_1 y_1 + ...
  const next = ended ? els(row[k])[0] : row[k];
  if (!next || k > i + 2) return false;
  const base = (e: Element) => (SCRIPTS.has(e.tagName) ? els(e)[0] : e);
  const letter = (e: Element | undefined) => !!e && e.tagName === "mi" && /^\p{L}$/u.test(text(e));
  if (letter(base(here)) && next.tagName === "mi" && FUNCTIONS.test(text(next))) return true; // "x times the sine of"
  if (!(here.tagName === "msub" && letter(base(here))) && !closes && !bars) return false;
  if (closes && /^[∫∬∮∑∏]$/.test(text(base(next)))) return true; // \Psi(t)\int ...: "Psi of t times the integral"
  if (closes && next.tagName === "mrow" && letter(els(next)[0])) return true; // \Psi(t)\Psi(0)^{-1}: "times Psi of 0 inverse"
  if (bars && isBarPair(next)) return true;
  if (!letter(base(next))) return false;
  return !(text(base(next)) === "d" && letter(row[k + 1])); // dx, dt
}

/** The pauses and the unwritten "times" in one row. */
function spacing(n: Element, doc: Document, inBars = false) {
  // not under an accent: \overline{z_1 z_2} read as a product loses its "conjugate of"
  if (/^m(?:over|under|underover)$/.test((n.parentNode as Element | null)?.tagName ?? "")) return;
  const row = els(n);
  for (let i = row.length - 1; i >= 0; i--) {
    const times = impliedTimes(row, i); // before the pause wraps row[i]
    if (needsEnd(row[i], row[i + 1], row[i + 2], inBars)) {
      // the pause goes inside, with the expression: between it and a minus, SRE takes the minus for a sign
      const ended = doc.createElementNS(MATHML, "mrow");
      ended.setAttribute("data-said", "ended");
      n.replaceChild(ended, row[i]);
      ended.appendChild(row[i]);
      ended.appendChild(word(doc, ","));
      row[i] = ended;
    }
    if (!times) continue;
    const dot = doc.createElementNS(MATHML, "mo");
    dot.appendChild(doc.createTextNode("⋅"));
    n.insertBefore(dot, row[i + 1]);
  }
}

/** Fix one element of KaTeX's MathML, children first. */
function fix(n: Element, doc: Document) {
  for (const c of els(n)) fix(c, doc);
  const kids = els(n);
  if (n.getAttribute("mathvariant") === "bold-italic") n.setAttribute("mathvariant", "bold"); // \boldsymbol: "bold xi"

  // y'' is two primes, said "prime prime"; one double prime is "double prime"
  if (n.tagName === "mrow" && kids.length >= 2 && kids.length <= 4 && kids.every((k) => k.tagName === "mo" && text(k) === "′")) {
    const mo = doc.createElementNS(MATHML, "mo");
    mo.appendChild(doc.createTextNode(PRIMES[kids.length]));
    n.parentNode?.replaceChild(mo, n);
    return;
  }

  if (n.tagName === "mfrac") {
    const said = fraction(n, doc);
    if (said) {
      n.parentNode?.replaceChild(said, n);
      return;
    }
  }

  if (n.tagName === "mover" && kids.length === 2 && kids[1].tagName === "mo") {
    const [base, accent] = kids;
    // \bar is U+02C9, which SRE has no name for, and \overline on one letter is a bar too: U+00AF is "bar"
    if (text(accent) === "ˉ" || (text(accent) === "‾" && base.tagName !== "mrow")) accent.textContent = "¯";
    if (text(accent) === VECTOR_ARROW) {
      // \vec is a combining arrow SRE reads as itself: "vector v"
      const row = doc.createElementNS(MATHML, "mrow");
      row.appendChild(word(doc, "vector "));
      row.appendChild(base);
      n.parentNode?.replaceChild(row, n);
      return;
    }
  }

  if (ROWS.has(n.tagName)) {
    for (const k of els(n)) {
      // \! and \colon leave <mtext>&#x2009;&#x2063;</mtext>, and SRE reads the invisible separator as "comma";
      // \colon's \mathpunct{} also leaves an empty <mo>, and aligned's &= an empty <mrow>.
      const t = k.textContent ?? "";
      if (k.tagName === "mtext" && BLANK.test(t) && INVISIBLE.test(t)) n.removeChild(k);
      else if ((k.tagName === "mo" || k.tagName === "mrow") && els(k).length === 0 && t.trim() === "") n.removeChild(k);
    }
    for (let i = 0; i + 2 < els(n).length; i++) {
      const [open, table, close] = els(n).slice(i, i + 3);
      if (!["mo", "mi"].includes(open.tagName) || table.tagName !== "mtable") continue;
      const said = matrix(open, table, close, doc);
      if (!said) continue;
      n.insertBefore(said, open);
      for (const x of [open, table, close]) n.removeChild(x);
    }
    for (const pair of pairBars(n, doc)) spacing(pair, doc, true); // the rows inside |...|, which are new
    spacing(n, doc);
  }

  if (n.tagName === "mtable") {
    const rows = els(n).filter((r) => r.tagName === "mtr");
    const align = n.getAttribute("columnalign") ?? "";
    const prev = n.previousSibling?.nodeType === 1 ? (n.previousSibling as Element) : null;
    // aligned, align, split: the columns only line things up, so each row is one formula ("blank equals c" otherwise)
    if (/^right left/.test(align)) {
      for (const r of rows) {
        const cells = els(r);
        const formula = doc.createElementNS(MATHML, "mrow");
        for (const c of cells) for (const x of contents(c)) formula.appendChild(x);
        for (const c of cells.slice(1)) r.removeChild(c);
        for (const x of Array.from(cells[0].childNodes)) cells[0].removeChild(x);
        cells[0].appendChild(formula);
      }
    }
    // cases: "x x is greater than 0" is "x if x is greater than 0", unless the condition has its own words, or the
    // second column starts with a relation (a system written with cases, x' &= y)
    if (prev?.tagName === "mo" && text(prev) === "{" && !n.nextSibling && /^left left/.test(align)) {
      for (const r of rows) {
        const cells = els(r);
        if (cells.length !== 2) continue;
        for (const cell of cells) {
          // "x, & x \ge 0," and "\text{otherwise.}": the punctuation is for the eye
          let last: Element = cell;
          while (els(last).length) last = els(last).at(-1)!;
          if (last.tagName === "mo" && /^[,.;]$/.test(text(last))) last.parentNode?.removeChild(last);
          else if (last.tagName === "mtext" && /[,.;]$/.test(last.textContent ?? "")) last.textContent = last.textContent!.replace(/[,.;]$/, "");
        }
        const first = contents(cells[1])[0];
        const relation = first?.tagName === "mo" && /^[=<>≤≥≠≈]$/.test(text(first));
        if (!relation && !/^\s*(if|when|for|otherwise|else|unless)\b/.test(cells[1].textContent ?? "")) {
          cells[1].insertBefore(word(doc, "if "), cells[1].firstChild);
        }
      }
    }
  }
}

function fixMathml(mathml: string): string {
  const doc = new DOMParser().parseFromString(mathml, "text/xml");
  if (doc.getElementsByTagName("parsererror").length) return mathml;
  fix(doc.documentElement, doc);
  return new XMLSerializer().serializeToString(doc);
}

/* ------------------------------------------------------------------------------------ the words SRE says badly */

const SPEECH: [RegExp, string][] = [
  [/\bthe metric of\b/g, "the norm of"], // ClearSpeak calls ‖x‖ a metric
  [/\bend metric\b/g, "end norm"],
  [/\bthe line segment\b/g, "the conjugate of"], // \overline over more than one letter: complex conjugates here
  [/\blamda\b/g, "lambda"], // SRE's Unicode spelling; the voice should see the usual one
  [/ dot above\b/g, " dot"], // \dot x
  [/ two dots\b/g, " double dot"], // \ddot x
  [/ divided by /g, " over "], // 1/n is "1 over n", as \frac{1}{n} is
  [/\balmost equals\b/g, "is approximately"],
  [/\bcap ([A-Z])\b/g, "$1"], // \mathrm{R}: "R", not "cap R"
  [/^open paren (\w+ sub \w+(?: sub \w+)?) close paren$/, "$1"], // (a_n), (s_{n_k}), but a tuple keeps its own
  [/ of open paren (\w+ sub \w+(?: sub \w+)?) close paren/g, " of $1"], // f(a_n)
  [/ comma(?= |,|$)/g, ","], // a pause, not the word: SRE says the punctuation in the words added above
  [/ colon(?= |$)/g, ":"],
  [/ Row (\d+):/g, ", row $1:"], // a matrix's rows, cases and lines, apart
  [/ Case (\d+):/g, ", case $1:"],
  [/ Line (\d+):/g, ", line $1:"],
  [/ end (matrix|vector)\b/g, ", end $1,"],
  [/(?:,| comma) dot dot dot(?:,| comma) /g, ", and so on up to "], // a_1, a_2, \ldots, a_n
  [/(?:,| comma) dot dot dot$/g, ", and so on"],
  // "for each epsilon is greater than 0" is "for each epsilon greater than 0" (but "such that n is greater than N"),
  // and "the supremum over x is in S of" is "over x in S"
  [/\b(for (?:every|each|all|any|some)|there (?:is|exists)(?: no| an?| some| a unique| a number)?|given|(?:supremum|infimum|maximum|minimum|sum|product|union|intersection) over|, for) ((?:(?!(?:such|that|where|so|and|then|implies)\b)[\w']+ ){1,4}?)is (?=(?:greater|less|at|in|not|equal)\b)/g, "$1 $2"],
  [/,(\s*,)+/g, ","],
  [/^[,\s]+/, ""], // a closing comma stays: "n plus 1, all over 2 n plus 1, equal to two thirds"
];

/** What a reply's math as a whole says about how to read each expression in it. */
export interface Reading {
  /** Capital letters the reply also uses in lowercase, said "capital N" so n and N aren't heard as the same. */
  capitals: ReadonlySet<string>;
  /** Letters the reply uses both bold and plain (vectors x and scalars x), said "vector x" when bold. */
  vectors: ReadonlySet<string>;
  /** The reply has matrices, so a_{12} is the entry "a sub 1 2", not the twelfth term. */
  entries: boolean;
}

const BOLD = /\\(?:mathbf|boldsymbol|bm|vec)\s*\{?\s*(\\[a-zA-Z]+|[a-zA-Z])\s*\}?/g;

/**
 * How to read a reply's math: its capital letters and bold letters that would otherwise sound like other letters
 * in it. A lowercase letter only counts on its own: a matrix A's entries a_{12} are heard as "a sub 1 2", not "a".
 */
export function reading(texs: string[]): Reading {
  const tex = texs.join(" ");
  const bold = new Set([...tex.matchAll(BOLD)].map((m) => m[1].replace(/^\\/, "")));
  const plainTex = tex.replace(BOLD, " ").replace(/\\mathbb\{[A-Z]\}/g, " ").replace(/\\(?:text|operatorname|mathrm)\{[^{}]*\}/g, " ");
  const greek = new Set([...plainTex.matchAll(/\\([a-zA-Z]+)/g)].map((m) => m[1]));
  const noCommands = plainTex.replace(/\\[a-zA-Z]+/g, " ");
  const alone = /(?<![a-zA-Z])[a-zA-Z](?![a-zA-Z])/g;
  const letters = new Set(noCommands.replace(/(?<![a-zA-Z])[a-z]\s*_/g, " ").match(alone) ?? []); // a, not a_{12}
  const plain = new Set(noCommands.match(alone) ?? []); // the scalars x_1 = y beside the vectors \mathbf{x}_1
  const capitals = new Set([...letters, ...(noCommands.match(/[A-Z]/g) ?? [])]); // and TDT's D
  const lowercase = new Set([...letters, ...bold]); // a matrix X beside its columns \mathbf{x}^{(1)}, said "x"
  return {
    capitals: new Set([...capitals].filter((c) => /[A-Z]/.test(c) && lowercase.has(c.toLowerCase()))),
    vectors: new Set([...bold].filter((b) => plain.has(b) || greek.has(b))),
    entries: /\\begin\{[pbvBV]?matrix\}|\\det(?![a-zA-Z])/.test(tex),
  };
}

const NONE: Reading = { capitals: new Set(), vectors: new Set(), entries: false };

/** How a LaTeX expression sounds, as part of a reply whose math is read as `context` says (reading()). */
export async function mathSpeech(tex: string, context: Reading = NONE): Promise<string> {
  await ready;
  if (context.entries) tex = tex.replace(/([a-zA-Z])_\{\s*([1-9])\s*([1-9])\s*\}/g, T`$1_{$2\,$3}`); // a_{12}: "a sub 1 2"
  const mathml = katex
    .renderToString(rewrite(tex), { output: "mathml", throwOnError: true, strict: false, macros: MACROS })
    .replace(/^<span class="katex">|<\/span>$/g, "")
    .replace(/<annotation[^>]*>[\s\S]*?<\/annotation>/, "") // the LaTeX itself, which SRE would read too
    .replace(/ mathvariant="normal"/g, ""); // or ∞ is "normal infinity"
  let speech = SRE.toSpeech(fixMathml(mathml));
  speech = speech.replace(/\bbold (\w+)/g, (_, w: string) => (context.vectors.has(w) ? `vector ${w}` : w)); // "x prime equals A x"
  for (const [pattern, words] of SPEECH) speech = speech.replace(pattern, words);
  if (context.capitals.size) speech = speech.replace(/\b[A-Z]\b/g, (c) => (context.capitals.has(c) ? `capital ${c}` : c));
  return speech;
}

// Shorthands tutors type that KaTeX doesn't know.
const MACROS = {
  "\\abs": "\\left|#1\\right|",
  "\\norm": "\\left\\|#1\\right\\|",
  "\\eps": "\\varepsilon",
  "\\R": "\\mathbb{R}",
  "\\N": "\\mathbb{N}",
  "\\Z": "\\mathbb{Z}",
  "\\Q": "\\mathbb{Q}",
  "\\C": "\\mathbb{C}",
};
