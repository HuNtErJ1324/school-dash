import type { Item, Kind, Status } from "../api";

export const KIND_LABEL: Partial<Record<Kind, string>> = {
  quiz: "Quiz",
  exam: "Exam",
  discussion: "Discussion",
  lecture: "Lecture",
  section: "Section",
  office_hours: "Office hours",
  release: "Released",
};

export const DEADLINE_KINDS: Kind[] = ["assignment", "quiz", "exam", "discussion"];
export const EVENT_KINDS: Kind[] = ["lecture", "section", "office_hours", "exam", "event"];

/** Statuses where the source itself says there's nothing left to do. */
export const DONE = new Set<Status | null>(["submitted", "graded", "excused"]);

/** Done by your own mark or by the source's word. */
export function isDone(item: Item): boolean {
  return item.done || DONE.has(item.status);
}

/**
 * A deadline that has passed and still needs you. Known-unfinished work always counts.
 * Unknown status counts for assignments, so you can tick them off; a past exam or
 * in-class quiz with no status just happened, it isn't overdue.
 */
export function isPastDue(item: Item, now: number): boolean {
  if (!item.due_at || isDone(item) || Date.parse(item.due_at) >= now) return false;
  if (item.status === "pending" || item.status === "missing") return true;
  return item.status === null && (item.kind === "assignment" || item.kind === "discussion");
}

export function TitleLink({ item, className }: { item: Item; className?: string }) {
  if (!item.url) return <span className={className}>{item.title}</span>;
  return (
    <a className={className} href={item.url} target="_blank" rel="noreferrer">
      {item.title}
    </a>
  );
}

const STATUS_TEXT: Partial<Record<Status, string>> = {
  missing: "Missing",
  submitted: "Submitted",
  graded: "Graded",
  excused: "Excused",
};

/** "18.5", "20": scores without trailing zeros. */
export function points(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, "");
}

export function StatusTag({ item }: { item: Item }) {
  const text = item.status && STATUS_TEXT[item.status];
  if (!text) return null;
  const score = item.status === "graded" && item.score !== null && item.points ? ` ${points(item.score)}/${points(item.points)}` : "";
  return <span className={`tag tag-${item.status}`}>{text + score}</span>;
}

export function CourseCode({ item }: { item: Item }) {
  return item.course ? <span className="course-code">{item.course.code}</span> : null;
}
