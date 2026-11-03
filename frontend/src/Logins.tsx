import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api, type SourceStatus } from "./api";
import { useNow, useSources, useTimezone } from "./hooks";
import { ago, shortWhen } from "./time";

const LABELS: Record<string, string> = { canvas: "Canvas", gradescope: "Gradescope" };

/** "Canvas", "Gradescope", or the source's own name. */
export const sourceLabel = (s: SourceStatus) => LABELS[s.type] ?? s.name;

/** A source that logs in with a cookie and needs a fresh one: it has none, or its last try was turned away. */
export function needsCookie(s: SourceStatus): boolean {
  return s.cookie !== null && (s.cookie.origin === null || s.cookie.check_ok === false);
}

/** Says so on every page when a login stops working (Canvas cookies last a few days), with the box to fix it. */
export function LoginNotice() {
  const sources = useSources();
  const now = useNow(60_000);
  const [fixed, setFixed] = useState<SourceStatus[]>([]);
  const broken = (sources.data ?? []).filter(needsCookie);
  return (
    <>
      {broken.map((s) => (
        <section key={s.name} className="login-notice" aria-label={`${sourceLabel(s)} login`}>
          <p>
            <b>{sourceLabel(s)}</b> isn't syncing:{" "}
            {s.cookie?.origin === null
              ? "it has no login cookie yet. Paste one to start it."
              : `its login was turned away${s.cookie?.checked_at ? ` ${ago(now - Date.parse(s.cookie.checked_at))}` : ""}, most likely because the session cookie expired. Paste a fresh one.`}
          </p>
          <CookieForm source={s} onSaved={(saved) => setFixed((list) => [...list.filter((f) => f.name !== s.name), saved])} />
        </section>
      ))}
      {fixed
        .filter((f) => !broken.some((s) => s.name === f.name))
        .map((f) => (
          <p key={f.name} className="login-notice fixed" role="status">
            <span>
              <b>{sourceLabel(f)}</b>'s new cookie works. {savedMessage(f)}
            </span>
            <button type="button" className="text-button" onClick={() => setFixed((list) => list.filter((x) => x.name !== f.name))}>
              Dismiss
            </button>
          </p>
        ))}
    </>
  );
}

/** For the source list: where the cookie came from and how its last check went, and a way to paste a new one. */
export function CookieLine({ source, now }: { source: SourceStatus; now: number }) {
  const tz = useTimezone();
  const [open, setOpen] = useState(false);
  const cookie = source.cookie;
  if (!cookie) return null;
  const where =
    cookie.origin === "pasted" && cookie.saved_at
      ? `Cookie pasted ${shortWhen(new Date(cookie.saved_at), now, tz)}`
      : cookie.origin === "config"
        ? "Cookie from .env"
        : "No cookie yet";
  const check = cookie.checked_at
    ? `; last check ${cookie.check_ok ? "worked" : "turned away"} ${ago(now - Date.parse(cookie.checked_at))}`
    : "";
  return (
    <div className="source-login">
      <span>
        {where}
        {check}.{" "}
        <button type="button" className="text-button" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Close" : "Paste a new cookie"}
        </button>
      </span>
      {open && <CookieForm source={source} />}
    </div>
  );
}

/** A box to paste a session cookie into. The server checks it, keeps it and syncs with it; it never comes back. */
function CookieForm({ source, onSaved }: { source: SourceStatus; onSaved?: (saved: SourceStatus) => void }) {
  const client = useQueryClient();
  const id = useId();
  const [text, setText] = useState("");
  const save = useMutation({
    mutationFn: () => api.saveCookie(source.name, text),
    onSuccess: (saved) => {
      setText("");
      client.setQueryData<SourceStatus[]>(["sources"], (list) => list?.map((s) => (s.name === saved.name ? saved : s)));
      for (const key of ["items", "courses", "grades", "quarters", "library"]) client.invalidateQueries({ queryKey: [key] });
      onSaved?.(saved);
    },
  });
  const site = source.cookie?.site ?? `${sourceLabel(source)}'s site`;
  return (
    <form
      className="cookie-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) save.mutate();
      }}
    >
      <label className="visually-hidden" htmlFor={id}>
        {sourceLabel(source)} session cookie
      </label>
      <textarea
        id={id}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          save.reset();
        }}
        rows={3}
        spellCheck={false}
        autoComplete="off"
        autoCapitalize="off"
        placeholder="The Cookie header, or the whole Copy as cURL"
      />
      <p className="field-help">
        From a tab where you're logged in to {site}: press F12, open Network and reload, then right-click the first
        request → Copy → Copy as cURL (either kind). Its Cookie request header works too.
      </p>
      <div className="cookie-form-actions">
        <button type="submit" className="pill-button" disabled={save.isPending || !text.trim()}>
          {save.isPending ? "Checking…" : "Check and save"}
        </button>
        {save.isError && <span className="problem">{save.error.message}</span>}
        {save.isSuccess && !onSaved && <span className="quiet-inline">{savedMessage(save.data)}</span>}
      </div>
    </form>
  );
}

function savedMessage(s: SourceStatus): string {
  const run = s.last_run;
  if (!run) return "Saved.";
  if (!run.ok) return `Saved, but the sync after it failed: ${run.error}`;
  return `Saved and synced, ${run.items_seen} ${run.items_seen === 1 ? "item" : "items"}.`;
}
