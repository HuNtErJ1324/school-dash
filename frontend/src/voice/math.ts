/**
 * Math read aloud: KaTeX turns the LaTeX into MathML, and Speech Rule Engine (what screen readers use) says it the
 * ClearSpeak way: "the limit as n goes to infinity of a sub n", "x squared plus 3 x". Loaded only when a reply with
 * math is read, since its rules are half a megabyte. They're served from the dashboard, not SRE's CDN.
 */
import katex from "katex";
import SRE from "speech-rule-engine/lib/sre.js";
import baseUrl from "speech-rule-engine/lib/mathmaps/base.json?url";
import enUrl from "speech-rule-engine/lib/mathmaps/en.json?url";

const MAPS: Record<string, string> = { base: baseUrl, en: enUrl };

const ready = (async () => {
  await SRE.setupEngine({
    locale: "en",
    domain: "clearspeak",
    modality: "speech",
    custom: async (locale: string) => {
      const res = await fetch(MAPS[locale] ?? MAPS.en);
      if (!res.ok) throw new Error(`speech rules for ${locale}: HTTP ${res.status}`);
      return res.text();
    },
  });
  await SRE.engineReady();
})();

const T = String.raw;

/** What ClearSpeak says badly, said the way a teacher would. */
export function rewrite(tex: string): string {
  return tex
    .replace(/\\lim_\{([^{}]*)\}/g, (_, under: string) => T`\text{the limit as }${under.replace(/\\to\b/g, T`\text{ goes to }`)}\text{ of }`)
    .replace(/\\binom\{([^{}]*)\}\{([^{}]*)\}/g, T`{$1}\text{ choose }{$2}`)
    .replace(/\\pmod\{([^{}]*)\}/g, T`\text{ mod }{$1}`)
    .replace(/\\mathbb\{([A-Z])\}(?=\s*\^)/g, T`\text{$1}`); // ℝ^n reads as "r-n" otherwise
}

/** How a LaTeX expression sounds. */
export async function mathSpeech(tex: string): Promise<string> {
  await ready;
  const mathml = katex
    .renderToString(rewrite(tex), { output: "mathml", throwOnError: false, strict: false })
    .replace(/^<span class="katex">|<\/span>$/g, "")
    .replace(/<annotation[^>]*>[\s\S]*?<\/annotation>/, "") // the LaTeX itself, which SRE would read too
    .replace(/ mathvariant="normal"/g, ""); // or ∞ is "normal infinity"
  return SRE.toSpeech(mathml);
}
