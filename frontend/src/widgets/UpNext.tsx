import { useMemo, useState } from "react";

import type { Item, Kind } from "../api";
import { courseStyle } from "../courses";
import { hourFloor, useItems, useNow, useTimezone } from "../hooks";
import { DoneToggle, ItemMenu, NoteLine } from "../items/ItemActions";
import { clock, countdownParts, DAY, dayLabel, relative, shortDay } from "../time";
import { CourseCode, DEADLINE_KINDS, isDone, isPastDue, KIND_LABEL, StatusTag, TitleLink } from "./parts";

export interface UpNextOptions {
  days: number; // how far ahead to look
  kinds: Kind[];
  courses: string[]; // empty means every course
  overdueDays: number; // how far back to look for unfinished work
  showDone: boolean; // keep done work in the list, crossed off
}

export const upNextDefaults: UpNextOptions = {
  days: 14,
  kinds: DEADLINE_KINDS,
  courses: [],
  overdueDays: 7,
  showDone: true,
};

export function UpNext({ options }: { options: UpNextOptions }) {
  const now = useNow();
  const tz = useTimezone();
  const hour = hourFloor(now);
  const [showHidden, setShowHidden] = useState(false);
  const courses = options.courses.length ? options.courses : undefined;
  const windowed = useItems({
    start: new Date(hour - options.overdueDays * DAY),
    end: new Date(hour + (options.days + 1) * DAY),
    kinds: options.kinds,
    courses,
    includeHidden: showHidden,
  });
  const pinnedQuery = useItems({ pinned: true, courses, includeHidden: showHidden });
  const { hero, pinned, pastDue, days } = useMemo(
    () => arrange(windowed.data ?? [], pinnedQuery.data ?? [], now, tz, options, showHidden),
    [windowed.data, pinnedQuery.data, now, tz, options, showHidden],
  );

  if (windowed.isPending) return <p className="quiet">Loading…</p>;
  if (windowed.isError) return <p className="problem">Can't reach the dashboard server. It retries every minute.</p>;

  const empty = !hero && pinned.length === 0 && pastDue.length === 0 && days.length === 0;
  return (
    <>
      {hero && <Hero item={hero} now={now} tz={tz} />}
      {pinned.length > 0 && <Group label="Pinned" items={pinned} now={now} tz={tz} showDate />}
      {pastDue.length > 0 && <Group label="Past due" late items={pastDue} now={now} tz={tz} showDate />}
      {days.map(([label, items]) => (
        <Group key={label} label={label} items={items} now={now} tz={tz} />
      ))}
      {empty && <p className="quiet">Nothing due in the next {options.days} days.</p>}
      <button type="button" className="text-button widget-footer-action" onClick={() => setShowHidden(!showHidden)}>
        {showHidden ? "Stop showing hidden items" : "Show hidden items"}
      </button>
    </>
  );
}

function arrange(windowed: Item[], pinnedItems: Item[], now: number, tz: string, o: UpNextOptions, showHidden: boolean) {
  // The server already leaves hidden items out; this also covers ones hidden a moment ago.
  const shown = (i: Item) => showHidden || !i.hidden;
  const items = windowed.filter(shown);
  const at = (i: Item) => Date.parse(i.at!);

  const ahead = items.filter((i) => i.at && at(i) >= now && at(i) <= now + o.days * DAY);
  const hero = ahead.find((i) => !isDone(i)) ?? null;
  const pinned = pinnedItems.filter((i) => shown(i) && !isDone(i) && i.id !== hero?.id);
  const elsewhere = new Set([hero?.id, ...pinned.map((i) => i.id)]);

  const pastDue = items.filter((i) => isPastDue(i, now) && !elsewhere.has(i.id));
  const days: [string, Item[]][] = []; // items arrive sorted, so consecutive grouping works
  for (const item of ahead) {
    if (elsewhere.has(item.id) || (!o.showDone && isDone(item))) continue;
    const label = dayLabel(new Date(item.at!), now, tz);
    const last = days.at(-1);
    if (last?.[0] === label) last[1].push(item);
    else days.push([label, [item]]);
  }
  return { hero, pinned, pastDue, days };
}

function when(item: Item, now: number, tz: string): string {
  const at = new Date(item.at!);
  const day = dayLabel(at, now, tz);
  const dayText = day === "Today" || day === "Tomorrow" ? day.toLowerCase() : day;
  // An exam isn't "due"; it just happens, so it gets no verb.
  const verb = item.kind === "exam" ? "" : item.due_at ? "Due " : "Starts ";
  const time = item.all_day ? `${dayText}, no time listed` : `${dayText} at ${clock(at, tz)}`;
  const text = `${verb}${time}${item.location ? ` in ${item.location}` : ""}`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function Hero({ item, now, tz }: { item: Item; now: number; tz: string }) {
  const ms = Date.parse(item.at!) - now;
  return (
    <article className="hero" style={courseStyle(item.course)}>
      <div className="hero-top">
        <p className="hero-course">
          <CourseCode item={item} />
          {KIND_LABEL[item.kind] && <span>{KIND_LABEL[item.kind]}</span>}
        </p>
        <ItemMenu item={item} />
      </div>
      <h3 className="hero-title">
        <DoneToggle item={item} large />
        <TitleLink item={item} />
      </h3>
      <p className="hero-count">
        {countdownParts(ms).map(([n, unit]) => (
          <span key={unit}>
            <b>{n}</b>
            <span className="unit">{unit}</span>
          </span>
        ))}
        <span className="left">{item.due_at && item.kind !== "exam" ? "left" : "to go"}</span>
      </p>
      <p className="hero-when">
        {when(item, now, tz)} <StatusTag item={item} />
      </p>
      <NoteLine note={item.note} />
    </article>
  );
}

function Group({ label, items, now, tz, late = false, showDate = false }: {
  label: string; items: Item[]; now: number; tz: string; late?: boolean; showDate?: boolean;
}) {
  return (
    <section className={late ? "group late" : "group"}>
      <h3 className="group-label">{label}</h3>
      <ul className="rows">
        {items.map((item) => (
          <Row key={item.id} item={item} now={now} tz={tz} showDate={showDate} />
        ))}
      </ul>
    </section>
  );
}

function Row({ item, now, tz, showDate }: { item: Item; now: number; tz: string; showDate: boolean }) {
  const at = new Date(item.at!);
  const today = dayLabel(at, now, tz) === "Today";
  const time = showDate && !today ? shortDay(at, tz) : item.all_day ? "No time listed" : clock(at, tz);
  const classes = ["row", isDone(item) && "done", item.hidden && "is-hidden"].filter(Boolean).join(" ");
  return (
    <li className={classes} style={courseStyle(item.course)}>
      <DoneToggle item={item} />
      <div className="row-main">
        <TitleLink item={item} className="row-title" />
        <span className="row-sub">
          <CourseCode item={item} />
          {KIND_LABEL[item.kind] && <span>{KIND_LABEL[item.kind]}</span>}
          <StatusTag item={item} />
          {item.hidden && <span className="tag">Hidden</span>}
        </span>
        <NoteLine note={item.note} />
      </div>
      <div className="row-when">
        <span className="row-time">{time}</span>
        <span className="row-rel">{relative(at.getTime() - now)}</span>
      </div>
      <ItemMenu item={item} />
    </li>
  );
}
