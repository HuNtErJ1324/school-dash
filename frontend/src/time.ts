// Every date shown is in the dashboard's configured timezone, not the device's,
// so a laptop and a phone always agree on what "today" means.

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const formatters = new Map<string, Intl.DateTimeFormat>();
function fmt(tz: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts);
  let f = formatters.get(key);
  if (!f) formatters.set(key, (f = new Intl.DateTimeFormat("en-US", { timeZone: tz, ...opts })));
  return f;
}

/** "2026-10-23": a sortable calendar-day key in the given timezone. */
export function dayKey(d: Date, tz: string): string {
  const parts = fmt(tz, { year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

// Calendar arithmetic on day keys, done in UTC so daylight-saving shifts can't skip or repeat a day.
function keyDate(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function addDays(key: string, n: number): string {
  const d = keyDate(key);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** 0 = Sunday. */
export function weekday(key: string): number {
  return keyDate(key).getUTCDay();
}

/** For a day key: "Mon", "26", and "Monday, Oct 26". */
export function keyParts(key: string): { short: string; day: string; long: string } {
  const d = keyDate(key);
  return {
    short: fmt("UTC", { weekday: "short" }).format(d),
    day: String(d.getUTCDate()),
    long: fmt("UTC", { weekday: "long", month: "short", day: "numeric" }).format(d),
  };
}

/** Minutes since local midnight in the given timezone. */
export function minutesIntoDay(d: Date, tz: string): number {
  const parts = fmt(tz, { hour: "numeric", minute: "numeric", hourCycle: "h23" }).formatToParts(d);
  const get = (t: string) => Number(parts.find((p) => p.type === t)!.value);
  return get("hour") * 60 + get("minute");
}

/** "9am", "12pm". */
export function hourLabel(hour: number): string {
  const h = hour % 12 || 12;
  return `${h}${hour < 12 || hour === 24 ? "am" : "pm"}`;
}

/** Compact "when" for tight spaces: "Today 11:59pm", "Mon 11:30am", "Nov 6 11:30am". */
export function shortWhen(d: Date, now: number, tz: string, allDay = false): string {
  const label = dayLabel(d, now, tz);
  const day =
    label === "Today" || label === "Tomorrow"
      ? label
      : d.getTime() - now < 6 * DAY && d.getTime() > now
        ? fmt(tz, { weekday: "short" }).format(d)
        : fmt(tz, { month: "short", day: "numeric" }).format(d);
  return allDay ? day : `${day} ${clock(d, tz)}`;
}

/** "Today", "Tomorrow", or "Wednesday, Oct 28". */
export function dayLabel(d: Date, now: number, tz: string): string {
  const key = dayKey(d, tz);
  if (key === dayKey(new Date(now), tz)) return "Today";
  if (key === dayKey(new Date(now + DAY), tz)) return "Tomorrow";
  return fmt(tz, { weekday: "long", month: "short", day: "numeric" }).format(d);
}

/** "11:59pm", "6pm". */
export function clock(d: Date, tz: string): string {
  return fmt(tz, { hour: "numeric", minute: "2-digit" })
    .format(d)
    .replace(":00", "")
    .replace(/\s?([AP])M/, (_, ap: string) => ap.toLowerCase() + "m");
}

/** "Thu, Oct 22". */
export function shortDay(d: Date, tz: string): string {
  return fmt(tz, { weekday: "short", month: "short", day: "numeric" }).format(d);
}

export function longDate(d: Date, tz: string): string {
  return fmt(tz, { weekday: "long", month: "long", day: "numeric" }).format(d);
}

/** Countdown parts for the hero: [["5","h"],["12","m"]]. */
export function countdownParts(ms: number): [string, string][] {
  if (ms < HOUR) return [[String(Math.max(1, Math.floor(ms / MINUTE))), "m"]];
  if (ms < DAY) return [[String(Math.floor(ms / HOUR)), "h"], [String(Math.floor((ms % HOUR) / MINUTE)), "m"]];
  if (ms < 7 * DAY) return [[String(Math.floor(ms / DAY)), "d"], [String(Math.floor((ms % DAY) / HOUR)), "h"]];
  return [[String(Math.round(ms / DAY)), " days"]];
}

/** "in 5h", "in 3d", "2d late". */
export function relative(ms: number): string {
  const abs = Math.abs(ms);
  const amount = abs < HOUR ? `${Math.max(1, Math.round(abs / MINUTE))}m` : abs < 48 * HOUR ? `${Math.round(abs / HOUR)}h` : `${Math.round(abs / DAY)}d`;
  return ms >= 0 ? `in ${amount}` : `${amount} late`;
}

/** "3 min ago", "2h ago". */
export function ago(ms: number): string {
  if (ms < MINUTE) return "just now";
  if (ms < HOUR) return `${Math.round(ms / MINUTE)} min ago`;
  if (ms < DAY) return `${Math.round(ms / HOUR)}h ago`;
  return `${Math.round(ms / DAY)}d ago`;
}

export { DAY, HOUR, MINUTE };
