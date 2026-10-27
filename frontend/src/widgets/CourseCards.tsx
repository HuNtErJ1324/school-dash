import { useMemo } from "react";

import type { Course, Item } from "../api";
import { courseStyle } from "../courses";
import { hourFloor, useCourses, useItems, useNow, useTimezone } from "../hooks";
import { DAY, relative, shortWhen } from "../time";
import { isDone, isPastDue, TitleLink } from "./parts";

export interface CourseCardsOptions {
  courses: string[]; // empty means every course with something coming up
  showOfficeHours: boolean;
  showLinks: boolean;
}

export const courseCardsDefaults: CourseCardsOptions = {
  courses: [],
  showOfficeHours: true,
  showLinks: true,
};

const LINK_ORDER = ["canvas", "site", "gradescope", "ed"];
const LINK_LABEL: Record<string, string> = { canvas: "Canvas", site: "Course site", gradescope: "Gradescope", ed: "Ed" };

interface Summary {
  course: Course;
  nextDue: Item | null;
  dueThisWeek: number;
  pastDue: number;
  nextExam: Item | null;
  nextClass: Item | null;
  nextOfficeHours: Item | null;
}

export function CourseCards({ options }: { options: CourseCardsOptions }) {
  const now = useNow();
  const tz = useTimezone();
  const hour = hourFloor(now);
  const courses = useCourses();
  const items = useItems({ start: new Date(hour - 7 * DAY), end: new Date(hour + 28 * DAY) });
  const cards = useMemo(() => {
    const chosen = (courses.data ?? []).filter((c) => !options.courses.length || options.courses.includes(c.id));
    return chosen.map((c) => summarize(c, (items.data ?? []).filter((i) => i.course?.id === c.id && !i.hidden), now));
  }, [courses.data, items.data, options.courses, now]);

  if (courses.isPending || items.isPending) return <p className="quiet">Loading…</p>;
  if (courses.isError || items.isError) return <p className="problem">Can't reach the dashboard server. It retries every minute.</p>;
  if (cards.length === 0) return <p className="quiet">No courses have anything scheduled yet.</p>;

  return (
    <ul className="course-cards">
      {cards.map((s) => (
        <CourseCard key={s.course.id} summary={s} options={options} now={now} tz={tz} />
      ))}
    </ul>
  );
}

function summarize(course: Course, items: Item[], now: number): Summary {
  const upcoming = (i: Item) => Date.parse(i.at!) >= now;
  const deadlines = items.filter((i) => i.due_at && i.kind !== "exam" && !isDone(i));
  return {
    course,
    nextDue: deadlines.find(upcoming) ?? null,
    dueThisWeek: deadlines.filter((i) => upcoming(i) && Date.parse(i.due_at!) < now + 7 * DAY).length,
    pastDue: items.filter((i) => isPastDue(i, now)).length,
    nextExam: items.find((i) => i.kind === "exam" && upcoming(i)) ?? null,
    nextClass: items.find((i) => (i.kind === "lecture" || i.kind === "section") && upcoming(i)) ?? null,
    // Office hours already underway still count.
    nextOfficeHours: items.find((i) => i.kind === "office_hours" && Date.parse(i.ends_at ?? i.at!) >= now) ?? null,
  };
}

function CourseCard({ summary: s, options, now, tz }: { summary: Summary; options: CourseCardsOptions; now: number; tz: string }) {
  const links = Object.entries(s.course.links).sort(
    ([a], [b]) => (LINK_ORDER.indexOf(a) + 1 || 99) - (LINK_ORDER.indexOf(b) + 1 || 99),
  );
  const at = (i: Item) => new Date(i.at!);
  return (
    <li className="course-card" style={courseStyle(s.course)}>
      <h3 className="course-card-code">{s.course.code}</h3>
      {s.course.name && <p className="course-card-name">{s.course.name.replace(/^.*?:\s*/, "")}</p>}

      <p className="course-card-counts">
        <span>{s.dueThisWeek === 0 ? "Nothing due this week" : `${s.dueThisWeek} due this week`}</span>
        {s.pastDue > 0 && <span className="problem">{s.pastDue} past due</span>}
      </p>

      <dl className="course-card-facts">
        {s.nextDue && (
          <>
            <dt>Next due</dt>
            <dd>
              <TitleLink item={s.nextDue} /> <span className="quiet-inline">{shortWhen(at(s.nextDue), now, tz, s.nextDue.all_day)}, {relative(at(s.nextDue).getTime() - now)}</span>
            </dd>
          </>
        )}
        {s.nextExam && (
          <>
            <dt>Next exam</dt>
            <dd>
              <TitleLink item={s.nextExam} /> <span className="quiet-inline">{shortWhen(at(s.nextExam), now, tz)}</span>
            </dd>
          </>
        )}
        {s.nextClass && (
          <>
            <dt>Next class</dt>
            <dd>
              {s.nextClass.title} <span className="quiet-inline">{shortWhen(at(s.nextClass), now, tz)}</span>
            </dd>
          </>
        )}
        {options.showOfficeHours && s.nextOfficeHours && (
          <>
            <dt>Office hours</dt>
            <dd>
              {s.nextOfficeHours.title}{" "}
              <span className="quiet-inline">
                {Date.parse(s.nextOfficeHours.at!) <= now ? "now" : shortWhen(at(s.nextOfficeHours), now, tz)}
                {s.nextOfficeHours.location && `, ${s.nextOfficeHours.location}`}
              </span>
            </dd>
          </>
        )}
      </dl>

      {options.showLinks && links.length > 0 && (
        <ul className="course-card-links">
          {links.map(([key, url]) => (
            <li key={key}>
              <a href={url} target="_blank" rel="noreferrer">
                {LINK_LABEL[key] ?? key.charAt(0).toUpperCase() + key.slice(1)}
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
