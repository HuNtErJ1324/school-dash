import type { ComponentType } from "react";

import type { Kind } from "../api";
import { Ask, askDefaults, type AskOptions } from "./Ask";
import { CourseCards, courseCardsDefaults, type CourseCardsOptions } from "./CourseCards";
import { Grades, gradesDefaults, type GradesOptions } from "./Grades";
import { Library, libraryDefaults, type LibraryOptions } from "./Library";
import { PlanWidget, planDefaults, type PlanOptions } from "./Plan";
import { Today, todayDefaults, type TodayOptions } from "./Today";
import { UpNext, upNextDefaults, type UpNextOptions } from "./UpNext";
import { Week, weekDefaults, type WeekOptions } from "./Week";

/** One setting in a widget's settings form. `key` names an option. */
export type Field<O> = { key: Extract<keyof O, string>; label: string; help?: string } & (
  | { type: "number"; min: number; max: number }
  | { type: "boolean" }
  | { type: "kinds"; choices: Kind[] }
  | { type: "courses" }
  | { type: "select"; choices: { value: string; label: string }[] }
);

export interface WidgetDefinition<O> {
  title: string;
  description: string; // shown in "Add widget"
  defaults: O;
  fields: Field<O>[];
  size: { w: number; h: number; minW: number; minH: number }; // in grid units, out of 12 columns
  component: ComponentType<{ options: O }>;
}

function define<O>(def: WidgetDefinition<O>): WidgetDefinition<O> {
  return def;
}

/** Every widget type. A new widget is a component plus one entry here. */
export const widgets = {
  plan: define<PlanOptions>({
    title: "Today's plan",
    description: "What to work on today, made each morning from what's due and your course materials.",
    defaults: planDefaults,
    fields: [
      { key: "showSteps", label: "Show each task's steps", type: "boolean" },
      { key: "showHeadsUp", label: "Show what's coming up", type: "boolean" },
    ],
    size: { w: 7, h: 13, minW: 3, minH: 5 },
    component: PlanWidget,
  }),
  ask: define<AskOptions>({
    title: "Ask",
    description: "Questions about your courses, answered from the course materials, with page references.",
    defaults: askDefaults,
    fields: [],
    size: { w: 7, h: 10, minW: 3, minH: 5 },
    component: Ask,
  }),
  library: define<LibraryOptions>({
    title: "Library",
    description: "The course documents the planner reads, and where you add textbooks and notes.",
    defaults: libraryDefaults,
    fields: [{ key: "courses", label: "Courses", type: "courses" }],
    size: { w: 6, h: 9, minW: 3, minH: 4 },
    component: Library,
  }),
  "up-next": define<UpNextOptions>({
    title: "Up next",
    description: "Deadlines in order, with a countdown to the nearest one.",
    defaults: upNextDefaults,
    fields: [
      { key: "days", label: "Days ahead", type: "number", min: 1, max: 90 },
      { key: "overdueDays", label: "Days back to look for past-due work", type: "number", min: 0, max: 30 },
      { key: "kinds", label: "Include", type: "kinds", choices: ["assignment", "quiz", "exam", "discussion"] },
      { key: "courses", label: "Courses", type: "courses" },
      { key: "showDone", label: "Keep done work in the list, crossed off", type: "boolean" },
    ],
    size: { w: 8, h: 10, minW: 3, minH: 4 },
    component: UpNext,
  }),
  today: define<TodayOptions>({
    title: "Today",
    description: "Today's classes, sections and office hours. Shows tomorrow once today is over.",
    defaults: todayDefaults,
    fields: [
      { key: "kinds", label: "Include", type: "kinds", choices: ["lecture", "section", "office_hours", "exam", "event"] },
      { key: "courses", label: "Courses", type: "courses" },
    ],
    size: { w: 4, h: 10, minW: 3, minH: 3 },
    component: Today,
  }),
  week: define<WeekOptions>({
    title: "Week",
    description: "A calendar of the coming days, with deadlines along the top.",
    defaults: weekDefaults,
    fields: [
      { key: "days", label: "Days to show", type: "number", min: 1, max: 14 },
      {
        key: "start",
        label: "Start on",
        type: "select",
        choices: [
          { value: "today", label: "Today" },
          { value: "monday", label: "This week's Monday" },
        ],
      },
      { key: "kinds", label: "Draw on the calendar", type: "kinds", choices: ["lecture", "section", "office_hours", "exam", "event"] },
      { key: "showDeadlines", label: "Show deadlines along the top", type: "boolean" },
      { key: "courses", label: "Courses", type: "courses" },
    ],
    size: { w: 12, h: 9, minW: 4, minH: 4 },
    component: Week,
  }),
  courses: define<CourseCardsOptions>({
    title: "Courses",
    description: "A card per course: what's due next, how much is due this week, and links.",
    defaults: courseCardsDefaults,
    fields: [
      { key: "courses", label: "Courses", type: "courses", help: "Leave all unchecked to show every course." },
      { key: "showOfficeHours", label: "Show the next office hours", type: "boolean" },
      { key: "showLinks", label: "Show links to Canvas, the course site and more", type: "boolean" },
    ],
    size: { w: 12, h: 7, minW: 3, minH: 3 },
    component: CourseCards,
  }),
  grades: define<GradesOptions>({
    title: "Grades",
    description: "Your estimated grade in each course, weighted the way the syllabus says.",
    defaults: gradesDefaults,
    fields: [{ key: "courses", label: "Courses", type: "courses" }],
    size: { w: 12, h: 8, minW: 3, minH: 4 },
    component: Grades,
  }),
};

export type WidgetType = keyof typeof widgets;

export function isWidgetType(type: string): type is WidgetType {
  return Object.hasOwn(widgets, type);
}

/** Widget definitions with their option types erased, for code that handles any widget. */
export function definition(type: WidgetType): WidgetDefinition<Record<string, unknown>> {
  return widgets[type] as unknown as WidgetDefinition<Record<string, unknown>>;
}

export const KIND_NAME: Record<Kind, string> = {
  assignment: "Assignments",
  quiz: "Quizzes",
  exam: "Exams",
  discussion: "Discussions",
  announcement: "Announcements",
  release: "Releases",
  lecture: "Lectures",
  section: "Sections",
  office_hours: "Office hours",
  event: "Other events",
  other: "Everything else",
};
