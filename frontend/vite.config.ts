import { cpSync, createReadStream, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { dirname, extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

export default defineConfig({
  plugins: [react(), excalidrawFonts(), vizLibraries()],
  // In dev, the API runs separately (backend/dev.py on :8765). In production FastAPI serves dist/.
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});

const FONTS = fileURLToPath(new URL("node_modules/@excalidraw/excalidraw/dist/prod/fonts", import.meta.url));

/**
 * The Canvas tab's fonts, served from /excalidraw/fonts (src/canvas/assets.ts points Excalidraw there)
 * instead of its CDN. All but Xiaolai: that's 13 MB of Chinese, Japanese and Korean glyphs, which
 * Excalidraw still fetches from its CDN if such text is ever drawn.
 */
function excalidrawFonts(): Plugin {
  return {
    name: "excalidraw-fonts",
    configureServer(server) {
      server.middlewares.use("/excalidraw/fonts", (req, res, next) => {
        const path = normalize(join(FONTS, decodeURIComponent((req.url ?? "").split("?")[0])));
        if (!path.startsWith(FONTS + sep) || !existsSync(path) || !statSync(path).isFile()) return next();
        res.setHeader("Content-Type", "font/woff2");
        createReadStream(path).pipe(res);
      });
    },
    writeBundle(options) {
      const into = join(options.dir ?? "dist", "excalidraw", "fonts");
      cpSync(FONTS, into, { recursive: true, filter: (src) => !src.includes("Xiaolai") });
    },
  };
}

const modules = (path: string) => fileURLToPath(new URL(`node_modules/${path}`, import.meta.url));
const KATEX_FONTS = modules("katex/dist/fonts");

/**
 * What the agent's diagrams can load (src/agent/Viz.tsx; the agent's AGENTS.md lists them), by their address
 * under /viz/. Diagrams run in a frame with no network, so these are the only libraries they get. Adding one is
 * a line here and a line in AGENTS.md.
 */
const VIZ: Record<string, string> = {
  "d3.js": modules("d3/dist/d3.min.js"),
  "p5.js": modules("p5/lib/p5.min.js"),
  "three/three.module.js": modules("three/build/three.module.js"),
  "three/three.core.js": modules("three/build/three.core.js"),
  "three/addons/controls/OrbitControls.js": modules("three/examples/jsm/controls/OrbitControls.js"),
  "three/addons/renderers/CSS2DRenderer.js": modules("three/examples/jsm/renderers/CSS2DRenderer.js"),
  "katex/katex.js": modules("katex/dist/katex.min.js"),
  "katex/katex.css": modules("katex/dist/katex.min.css"),
  ...Object.fromEntries(
    readdirSync(KATEX_FONTS)
      .filter((name) => name.endsWith(".woff2")) // katex.css lists woff2 first, and every browser takes it
      .map((name) => [`katex/fonts/${name}`, join(KATEX_FONTS, name)]),
  ),
  "fonts/CommitMonoNF-Regular.woff2": fileURLToPath(new URL("src/fonts/CommitMonoNF-Regular.woff2", import.meta.url)),
  "fonts/CommitMonoNF-Bold.woff2": fileURLToPath(new URL("src/fonts/CommitMonoNF-Bold.woff2", import.meta.url)),
};

const TYPES: Record<string, string> = { ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2" };

/**
 * VIZ, served from /viz/ in dev and copied into dist/viz/ by the build. A diagram's frame has no origin of its
 * own, so its module scripts and fonts are cross-origin requests that need the CORS header (main.py sends it in
 * production).
 */
function vizLibraries(): Plugin {
  return {
    name: "viz-libraries",
    configureServer(server) {
      server.middlewares.use("/viz", (req, res, next) => {
        const path = VIZ[decodeURIComponent((req.url ?? "").split("?")[0]).replace(/^\//, "")];
        if (!path) return next();
        res.setHeader("Content-Type", TYPES[extname(path)] ?? "application/octet-stream");
        res.setHeader("Access-Control-Allow-Origin", "*");
        createReadStream(path).pipe(res);
      });
    },
    writeBundle(options) {
      for (const [name, path] of Object.entries(VIZ)) {
        const into = join(options.dir ?? "dist", "viz", name);
        mkdirSync(dirname(into), { recursive: true });
        cpSync(path, into);
      }
    },
  };
}
