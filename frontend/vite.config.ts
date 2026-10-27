import { cpSync, createReadStream, existsSync, statSync } from "node:fs";
import { join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

export default defineConfig({
  plugins: [react(), excalidrawFonts()],
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
