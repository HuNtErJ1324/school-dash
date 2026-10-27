import { convertToExcalidrawElements, exportToBlob, FONT_FAMILY, getCommonBounds } from "@excalidraw/excalidraw";
import type { ExcalidrawElementSkeleton } from "@excalidraw/excalidraw/data/transform";
import type { ExcalidrawElement, FileId } from "@excalidraw/excalidraw/element/types";
import type { BinaryFileData, DataURL, ExcalidrawImperativeAPI } from "@excalidraw/excalidraw/types";

import { type Action, type Point } from "./reply";

export { type Action, parseReply } from "./reply";

/** Where a snapshot sits on the canvas: a pixel (px, py) in it is the point (x + px / scale, y + py / scale). */
export interface Frame {
  x: number;
  y: number;
  scale: number;
}

export const IDENTITY: Frame = { x: 0, y: 0, scale: 1 };

const PADDING = 24;
// Small enough that Claude sees the picture as sent, unscaled, so the pixels it names are the ones we drew:
// the long side at most 1568 px and about 1.15 megapixels. Small drawings are enlarged up to 2x to read.
const MAX_SIDE = 1568;
const MAX_PIXELS = 1_150_000;
const MAX_SCALE = 2;

/** A tutor's note, in canvas units. Its height grows with the text; this is a typical two lines. */
export const NOTE = { width: 260, height: 90 };

/** A picture of everything on the canvas, on white, and where it sits. Null when the canvas is empty. */
export async function snapshot(api: ExcalidrawImperativeAPI): Promise<{ blob: Blob; frame: Frame; width: number; height: number } | null> {
  const elements = api.getSceneElements();
  if (!elements.length) return null;
  const [minX, minY, maxX, maxY] = getCommonBounds(elements);
  const w = maxX - minX + 2 * PADDING;
  const h = maxY - minY + 2 * PADDING;
  const scale = Math.min(MAX_SCALE, MAX_SIDE / w, MAX_SIDE / h, Math.sqrt(MAX_PIXELS / (w * h)));
  const width = Math.max(1, Math.floor(w * scale));
  const height = Math.max(1, Math.floor(h * scale));
  const blob = await exportToBlob({
    elements,
    files: api.getFiles(),
    mimeType: "image/png",
    exportPadding: PADDING,
    appState: { exportBackground: true, viewBackgroundColor: "#ffffff", exportWithDarkMode: false },
    getDimensions: () => ({ width, height, scale }),
  });
  return { blob, width, height, frame: { x: minX - PADDING, y: minY - PADDING, scale } };
}

// The tutor writes in foam on the overlay, so its notes never pass for the student's own work. Excalidraw's dark
// mode inverts the drawing, so these come out as the site's --foam and --bg-inset; exported light for the
// tutor's snapshots, they're dark teal on pale lavender.
const INK = "#003b46";
const NOTE_FILL = "#e8e5ff";

/** Marks what the tutor drew, with the reply it came from. */
export const tutorMark = (messageId: number) => ({ tutor: messageId });

/** The tutor's notes, arrows and circles from one reply, placed by the frame of the snapshot it looked at. */
export function draw(actions: Action[], frame: Frame, messageId: number): ExcalidrawElement[] {
  const toCanvas = ([px, py]: Point): Point => [frame.x + px / frame.scale, frame.y + py / frame.scale];
  const out: ExcalidrawElement[] = [];
  actions.forEach((a, i) => {
    const group = `tutor-${messageId}-${i}`;
    let parts: ExcalidrawElement[] = [];
    if ("note" in a) {
      const [x, y] = toCanvas(a.at);
      parts = convertToExcalidrawElements([
        {
          type: "rectangle",
          x,
          y,
          width: NOTE.width,
          height: 40, // grows to fit the text
          strokeColor: INK,
          backgroundColor: NOTE_FILL,
          fillStyle: "solid",
          strokeWidth: 1,
          roughness: 0,
          roundness: { type: 3 },
          label: { text: a.note, fontSize: 16, fontFamily: FONT_FAMILY.Nunito, textAlign: "left", verticalAlign: "top", strokeColor: INK },
        },
      ]);
      const box = parts.find((e) => e.type === "rectangle");
      if (a.point_to && box) {
        const [tx, ty] = toCanvas(a.point_to);
        // From the edge of the note nearest the spot; no arrow when the note sits on it.
        const sx = Math.min(Math.max(tx, box.x), box.x + box.width);
        const sy = Math.min(Math.max(ty, box.y), box.y + box.height);
        if (Math.hypot(tx - sx, ty - sy) > 12) parts.push(...convertToExcalidrawElements([arrow(sx, sy, tx, ty)]));
      }
    } else if ("circle" in a) {
      const [x, y] = toCanvas([a.circle[0], a.circle[1]]);
      const pad = 8;
      parts = convertToExcalidrawElements([
        {
          type: "ellipse",
          x: x - pad,
          y: y - pad,
          width: a.circle[2] / frame.scale + 2 * pad,
          height: a.circle[3] / frame.scale + 2 * pad,
          strokeColor: INK,
          backgroundColor: "transparent",
          strokeWidth: 2,
          roughness: 1,
        },
      ]);
    }
    out.push(...parts.map((e) => ({ ...e, groupIds: [group], customData: tutorMark(messageId) })));
  });
  return out;
}

function arrow(sx: number, sy: number, tx: number, ty: number): ExcalidrawElementSkeleton {
  return {
    type: "arrow",
    x: sx,
    y: sy,
    points: [
      [0, 0],
      [tx - sx, ty - sy],
    ],
    strokeColor: INK,
    strokeWidth: 2,
    roughness: 0,
    endArrowhead: "arrow",
  } as ExcalidrawElementSkeleton;
}

/** An image file for Excalidraw from a data URL. */
export function imageFile(id: string, dataURL: string, mimeType: string): BinaryFileData {
  return { id: id as FileId, dataURL: dataURL as DataURL, mimeType: mimeType as BinaryFileData["mimeType"], created: Date.now() };
}

export const dataUrlToBlob = async (dataURL: string): Promise<Blob> => (await fetch(dataURL)).blob();

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
