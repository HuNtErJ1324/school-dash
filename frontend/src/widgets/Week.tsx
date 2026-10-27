import { useMemo, useRef, type CSSProperties } from "react";

import type { Item, Kind } from "../api";
import { courseStyle } from "../courses";
import { hourFloor, useItems, useNow, useTimezone, useWidth } from "../hooks";
import { addDays, clock, DAY, dayKey, hourLabel, keyParts, minutesIntoDay, weekday } from "../time";
import { CourseCode, DEADLINE_KINDS, isDone, KIND_LABEL, TitleLink } from "./parts";

export interface WeekOptions {
  days: number;
  start: "today" | "monday";
  kinds: Kind[]; // what to draw on the time grid
  showDeadlines: boolean;
  courses: string[]; // empty means every course
}

export const weekDefaults: WeekOptions = {
  days: 7,
  start: "today",
  kinds: ["lecture", "section", "exam", "event"],
  showDeadlines: true,
  courses: [],
};

const NARROW = 520; // below this width, a list reads better than a grid
const MIN_BLOCK = 30; // minutes: shortest block drawn, so it stays tappable
const DEFAULT_LENGTH = 50; // minutes, for events with no end time

interface Placed {
  item: Item;
  start: number; // minutes into the day
  end: number;
  lane: number;
  lanes: number;
}

interface Day {
  key: string;
  events: Placed[];
  top: Item[]; // deadlines and all-day items
}

export function Week({ options }: { options: WeekOptions }) {
  const now = useNow();
  const tz = useTimezone();
  const hour = hourFloor(now);
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);

  const todayKey = dayKey(new Date(now), tz);
  const firstKey = options.start === "monday" ? addDays(todayKey, -((weekday(todayKey) + 6) % 7)) : todayKey;
  const keys = useMemo(() => Array.from({ length: options.days }, (_, i) => addDays(firstKey, i)), [firstKey, options.days]);
  const kinds = options.showDeadlines ? [...new Set([...options.kinds, ...DEADLINE_KINDS])] : options.kinds;
  const query = useItems({
    // Generous bounds; items are cut to exact days below.
    start: new Date(hour - 8 * DAY),
    end: new Date(hour + (options.days + 1) * DAY),
    kinds,
    courses: options.courses.length ? options.courses : undefined,
  });
  const days = useMemo(() => bucket(query.data ?? [], keys, tz, options), [query.data, keys, tz, options]);

  let body;
  if (query.isPending) body = <p className="quiet">Loading…</p>;
  else if (query.isError) body = <p className="problem">Can't reach the dashboard server. It retries every minute.</p>;
  else if (width > 0 && width < NARROW) body = <WeekList days={days} todayKey={todayKey} now={now} tz={tz} />;
  else if (width > 0) body = <WeekGrid days={days} todayKey={todayKey} now={now} tz={tz} />;
  return (
    <div ref={ref} className="week">
      {body}
    </div>
  );
}

function bucket(items: Item[], keys: string[], tz: string, o: WeekOptions): Day[] {
  const days = new Map(keys.map((key) => [key, { key, events: [] as Item[], top: [] as Item[] }]));
  for (const item of items) {
    if (item.hidden) continue;
    if (item.starts_at && o.kinds.includes(item.kind)) {
      const day = days.get(dayKey(new Date(item.starts_at), tz));
      if (!day) continue;
      (item.all_day ? day.top : day.events).push(item);
    } else if (o.showDeadlines && item.due_at && item.kind !== "exam") {
      days.get(dayKey(new Date(item.due_at), tz))?.top.push(item);
    }
  }
  return [...days.values()].map((d) => ({ key: d.key, top: d.top, events: place(d.events, tz) }));
}

/** Position events in a day, putting overlapping ones side by side in lanes. */
function place(events: Item[], tz: string): Placed[] {
  const blocks = events
    .map((item) => {
      const start = minutesIntoDay(new Date(item.starts_at!), tz);
      let end = item.ends_at ? minutesIntoDay(new Date(item.ends_at), tz) : start + DEFAULT_LENGTH;
      if (end <= start) end = 24 * 60; // runs past midnight
      return { item, start, end: Math.max(end, start + MIN_BLOCK), lane: 0, lanes: 1 };
    })
    .sort((a, b) => a.start - b.start || b.end - a.end);

  const out: Placed[] = [];
  let cluster: Placed[] = [];
  let clusterEnd = -1;
  const flush = () => {
    const laneEnds: number[] = [];
    for (const b of cluster) {
      const free = laneEnds.findIndex((end) => end <= b.start);
      b.lane = free === -1 ? laneEnds.length : free;
      laneEnds[b.lane] = b.end;
    }
    for (const b of cluster) b.lanes = laneEnds.length;
    out.push(...cluster);
    cluster = [];
  };
  for (const b of blocks) {
    if (b.start >= clusterEnd) flush();
    cluster.push(b);
    clusterEnd = Math.max(clusterEnd, b.end);
  }
  flush();
  return out;
}

function WeekGrid({ days, todayKey, now, tz }: { days: Day[]; todayKey: string; now: number; tz: string }) {
  const all = days.flatMap((d) => d.events);
  const firstHour = Math.min(9, ...all.map((b) => Math.floor(b.start / 60)));
  const lastHour = Math.max(17, ...all.map((b) => Math.ceil(b.end / 60)));
  const span = (lastHour - firstHour) * 60;
  const pct = (minutes: number) => `${((minutes - firstHour * 60) / span) * 100}%`;
  const nowMinutes = minutesIntoDay(new Date(now), tz);
  const hasTop = days.some((d) => d.top.length > 0);
  const style = { "--days": days.length, "--hours": lastHour - firstHour } as CSSProperties;

  return (
    <div className="week-grid" style={style}>
      <div className="week-row week-head">
        <span className="week-gutter" />
        {days.map((d) => {
          const { short, day } = keyParts(d.key);
          return (
            <div key={d.key} className={d.key === todayKey ? "week-day today" : "week-day"}>
              <span>{short}</span> <b>{day}</b>
            </div>
          );
        })}
      </div>

      {hasTop && (
        <div className="week-row week-top">
          <span className="week-gutter week-gutter-label">Due</span>
          {days.map((d) => (
            <div key={d.key} className="week-top-cell">
              {d.top.map((item) => (
                <Chip key={item.id} item={item} tz={tz} />
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="week-row week-body">
        <div className="week-gutter week-hours" aria-hidden="true">
          {Array.from({ length: lastHour - firstHour }, (_, i) => (
            <span key={i} style={{ top: pct((firstHour + i) * 60) }}>
              {hourLabel(firstHour + i)}
            </span>
          ))}
        </div>
        {days.map((d) => (
          <div key={d.key} className={d.key === todayKey ? "week-col today" : "week-col"}>
            {d.events.map((b) => (
              <Block key={b.item.id} block={b} top={pct(b.start)} height={`${((b.end - b.start) / span) * 100}%`} now={now} tz={tz} />
            ))}
            {d.key === todayKey && nowMinutes >= firstHour * 60 && nowMinutes <= lastHour * 60 && (
              <div className="now-line" style={{ top: pct(nowMinutes) }} aria-label="Now" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Block({ block, top, height, now, tz }: { block: Placed; top: string; height: string; now: number; tz: string }) {
  const { item, lane, lanes } = block;
  const start = new Date(item.starts_at!);
  const end = item.ends_at ? new Date(item.ends_at) : null;
  const past = (end ?? start).getTime() < now;
  const time = end && end > start ? `${clock(start, tz)}–${clock(end, tz)}` : clock(start, tz);
  const style = {
    ...courseStyle(item.course),
    top,
    height,
    left: `calc(${(lane / lanes) * 100}% + 1px)`,
    width: `calc(${100 / lanes}% - 2px)`,
  };
  const label = [item.title, item.course?.code, time, item.location].filter(Boolean).join(", ");
  const Tag = item.url ? "a" : "div";
  const short = block.end - block.start < 75; // room for one line of title, not two
  return (
    <Tag
      className={["week-block", past && "past", short && "short"].filter(Boolean).join(" ")}
      style={style}
      title={label}
      aria-label={label}
      {...(item.url ? { href: item.url, target: "_blank", rel: "noreferrer" } : {})}
    >
      <span className="week-block-title">{item.title}</span>
      <span className="week-block-meta">
        {clock(start, tz)} {item.course?.code}
      </span>
    </Tag>
  );
}

function Chip({ item, tz }: { item: Item; tz: string }) {
  const at = new Date(item.due_at ?? item.starts_at!);
  const time = item.all_day ? null : clock(at, tz);
  return (
    <span className={isDone(item) ? "week-chip done" : "week-chip"} style={courseStyle(item.course)} title={[item.title, item.course?.code, time].filter(Boolean).join(", ")}>
      {time && <span className="week-chip-time">{time}</span>} <TitleLink item={item} />
    </span>
  );
}

function WeekList({ days, todayKey, now, tz }: { days: Day[]; todayKey: string; now: number; tz: string }) {
  return (
    <>
      {days.map((d) => {
        const at = (i: Item) => Date.parse(i.starts_at ?? i.due_at!);
        const entries = [...d.top, ...d.events.map((b) => b.item)].sort((a, b) => at(a) - at(b));
        const label = d.key === todayKey ? "Today" : d.key === addDays(todayKey, 1) ? "Tomorrow" : keyParts(d.key).long;
        return (
          <section key={d.key} className="group">
            <h3 className="group-label">{label}</h3>
            {entries.length === 0 ? (
              <p className="quiet">Nothing scheduled.</p>
            ) : (
              <ol className="agenda">
                {entries.map((item) => {
                  const start = new Date(item.starts_at ?? item.due_at!);
                  const end = item.ends_at ? new Date(item.ends_at) : null;
                  const past = (end ?? start).getTime() < now;
                  const deadline = item.due_at && item.kind !== "exam";
                  const time = deadline
                    ? item.all_day ? "Due, no time listed" : `Due ${clock(start, tz)}`
                    : item.all_day
                      ? "All day"
                      : end && end > start ? `${clock(start, tz)}–${clock(end, tz)}` : clock(start, tz);
                  return (
                    <li key={item.id} className={past || isDone(item) ? "slot past" : "slot"} style={courseStyle(item.course)}>
                      <span className="slot-time">{time}</span>
                      <span className="slot-what">
                        <TitleLink item={item} className="slot-title" />
                      </span>
                      <span className="slot-sub">
                        <CourseCode item={item} />
                        {KIND_LABEL[item.kind] && !item.title.startsWith(KIND_LABEL[item.kind]!) && <span>{KIND_LABEL[item.kind]}</span>}
                        {item.location && <span>{item.location}</span>}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>
        );
      })}
    </>
  );
}
