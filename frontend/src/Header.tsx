import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type SourceStatus } from "./api";
import { useNow, useSources, useTimezone } from "./hooks";
import { useQuarter } from "./quarter";
import { Link } from "./router";
import { ago, longDate } from "./time";

export type Page = "board" | "tutor" | "canvas";

export function Header({ page, editing, onToggleEditing }: {
  page: Page;
  editing: boolean;
  onToggleEditing: () => void;
}) {
  const now = useNow(60_000);
  const tz = useTimezone();
  return (
    <header className="masthead">
      <div className="masthead-date">
        <h1 className="date">{longDate(new Date(now), tz)}</h1>
        <QuarterPicker />
      </div>
      <nav className="tabs" aria-label="Pages">
        <Link to="/" aria-current={page === "board" ? "page" : undefined}>
          Board
        </Link>
        <Link to="/tutor" aria-current={page === "tutor" ? "page" : undefined}>
          Tutor
        </Link>
        <Link to="/canvas" aria-current={page === "canvas" ? "page" : undefined}>
          Canvas
        </Link>
      </nav>
      <div className="masthead-actions">
        <SyncStatus now={now} />
        {page === "board" && (
          <button
            type="button"
            className={editing ? "pill-button edit-toggle" : "outline-button edit-toggle"}
            aria-pressed={editing}
            onClick={onToggleEditing}
          >
            {editing ? "Done editing" : "Edit board"}
          </button>
        )}
      </div>
    </header>
  );
}

/** Which quarter the dashboard shows: the current one unless you pick another. */
function QuarterPicker() {
  const { selected, current, quarters, select } = useQuarter();
  if (!selected) return null;
  return (
    <label className="quarter-picker">
      <span className="visually-hidden">Quarter</span>
      <select value={selected} onChange={(e) => select(e.target.value)}>
        {quarters.map((q) => (
          <option key={q.code} value={q.code}>
            {q.name}
            {q.code === current ? " (now)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function failing(s: SourceStatus): boolean {
  return Boolean(s.config_error) || s.last_run?.ok === false;
}

function SyncStatus({ now }: { now: number }) {
  const sources = useSources();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const sync = useMutation({
    mutationFn: api.syncAll,
    onSuccess: (statuses) => {
      client.setQueryData(["sources"], statuses);
      return client.invalidateQueries({ queryKey: ["items"] });
    },
  });

  useEffect(() => {
    if (!open) return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  const list = sources.data ?? [];
  const broken = list.filter(failing);
  const lastSuccess = Math.max(0, ...list.map((s) => (s.last_success_at ? Date.parse(s.last_success_at) : 0)));
  const summary = sources.isError
    ? "Can't reach the server"
    : broken.length === 1
      ? "1 source isn't syncing"
      : broken.length > 1
        ? `${broken.length} sources aren't syncing`
        : lastSuccess
          ? `Synced ${ago(now - lastSuccess)}`
          : list.length
            ? "Waiting for the first sync"
            : "No sources set up";

  return (
    <div className="sync">
      <button
        type="button"
        className={broken.length || sources.isError ? "sync-summary problem" : "sync-summary"}
        aria-expanded={open}
        aria-controls="source-list"
        onClick={() => setOpen(!open)}
      >
        {summary}
      </button>
      <button type="button" className="sync-now" onClick={() => sync.mutate()} disabled={sync.isPending}>
        {sync.isPending ? "Syncing…" : "Sync now"}
      </button>
      {open && (
        <ul id="source-list" className="sources">
          {list.length === 0 && <li className="source quiet">Add sources to config.yaml, then restart the server.</li>}
          {list.map((s) => (
            <li key={s.name} className={failing(s) ? "source problem" : "source"}>
              <span className="source-name">{s.name}</span>
              <span className="source-state">{describe(s, now)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function describe(s: SourceStatus, now: number): string {
  if (s.config_error) return `Not started: ${s.config_error}`;
  if (s.running) return "Syncing…";
  if (!s.last_run) return "Waiting for the first sync";
  if (!s.last_run.ok) return `Failed ${ago(now - Date.parse(s.last_run.finished_at))}: ${s.last_run.error}`;
  const n = s.last_run.items_seen;
  return `Synced ${ago(now - Date.parse(s.last_run.finished_at))}, ${n} ${n === 1 ? "item" : "items"}`;
}
