import type { CSSProperties } from "react";

import type { Course } from "./api";

// The server gives every course a distinct pencil (backend/app/courses.py). Each shows as a Rosé Pine chart
// hue: justin-chae.org's --chart-* steps, plus three more in the same lightness band, checked as a set with the
// dataviz palette validator against the panels (#1f1d2e) and the page: the first three stay apart in every
// pair (your current courses show together), all eight apart from their neighbors. A color you set in
// config.yaml that isn't a pencil shows as it is.
const HUE: Record<string, string> = {
  "#2f6fd0": "#23a0cb", // blue
  "#d9622b": "#c08934", // gold
  "#0f9a75": "#c24a6f", // love
  "#b88200": "#7f9b3b", // green
  "#6a4cd0": "#a681d0", // iris
  "#3b8a1e": "#c96f3c", // orange
  "#c23f86": "#2fa58a", // teal
  "#96592a": "#b05455", // rose
};

export function courseColor(course: Course | null): string {
  return course?.color ?? "var(--muted)";
}

export function courseStyle(course: Course | null): CSSProperties {
  const color = courseColor(course);
  const hue = HUE[color.toLowerCase()];
  return { "--course": color, ...(hue && { "--course-hue": hue }) } as CSSProperties;
}
