import type { Item, Kind } from "../api";
import { courseStyle } from "../courses";
import { hourFloor, useItems, useNow, useTimezone } from "../hooks";
import { clock, DAY, dayKey } from "../time";
import { CourseCode, EVENT_KINDS, KIND_LABEL, TitleLink } from "./parts";

export interface TodayOptions {
  kinds: Kind[];
  courses: string[]; // empty means every course
}

export const todayDefaults: TodayOptions = {
  kinds: EVENT_KINDS,
  courses: [],
};

export function Today({ options }: { options: TodayOptions }) {
  const now = useNow();
  const tz = useTimezone();
  const hour = hourFloor(now);
  // Fetch a generous window and cut it to calendar days in the dashboard's timezone.
  const query = useItems({
    start: new Date(hour - DAY),
    end: new Date(hour + 2 * DAY),
    kinds: options.kinds,
    courses: options.courses.length ? options.courses : undefined,
  });

  if (query.isPending) return <p className="quiet">Loading…</p>;
  if (query.isError) return <p className="problem">Can't reach the dashboard server. It retries every minute.</p>;

  const onDay = (d: number) => {
    const key = dayKey(new Date(d), tz);
    return query.data.filter((i) => i.starts_at && dayKey(new Date(i.starts_at), tz) === key);
  };
  const today = onDay(now);
  // Once today is over, its agenda is just history: look at tomorrow instead.
  const todayOver = today.every((i) => Date.parse(i.ends_at ?? i.starts_at!) < now);

  return (
    <>
      {today.length === 0 ? (
        <p className="quiet">No classes, sections or office hours today.</p>
      ) : todayOver ? (
        <p className="quiet">Today's classes and office hours are over.</p>
      ) : (
        <Agenda items={today} now={now} tz={tz} />
      )}
      {todayOver && <Tomorrow items={onDay(now + DAY)} now={now} tz={tz} />}
    </>
  );
}

function Tomorrow({ items, now, tz }: { items: Item[]; now: number; tz: string }) {
  return (
    <section className="group">
      <h3 className="group-label">Tomorrow</h3>
      {items.length ? <Agenda items={items} now={now} tz={tz} /> : <p className="quiet">Nothing scheduled.</p>}
    </section>
  );
}

function Agenda({ items, now, tz }: { items: Item[]; now: number; tz: string }) {
  return (
    <ol className="agenda">
      {items.map((item) => {
        const start = new Date(item.starts_at!);
        const end = item.ends_at ? new Date(item.ends_at) : start;
        const state = end.getTime() < now ? "past" : start.getTime() <= now ? "now" : "later";
        const kind = KIND_LABEL[item.kind];
        return (
          <li key={item.id} className={`slot ${state}`} style={courseStyle(item.course)}>
            <span className="slot-time">
              {item.all_day ? "All day" : end > start ? `${clock(start, tz)}–${clock(end, tz)}` : clock(start, tz)}
            </span>
            <span className="slot-what">
              <TitleLink item={item} className="slot-title" />
              {state === "now" && <span className="tag tag-now">Now</span>}
            </span>
            <span className="slot-sub">
              <CourseCode item={item} />
              {kind && !item.title.startsWith(kind) && <span>{kind}</span>}
              {item.location && <span>{item.location}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
