// Excalidraw loads its fonts from here first (vite.config.ts serves them), then from its CDN. Imported
// before Excalidraw, so the path is set when it registers its fonts.
(window as { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH = "/excalidraw/";

export {};
