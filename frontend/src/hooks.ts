import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type RefObject } from "react";

import { api, itemParams, type Item, type ItemQuery, type OverlayPatch } from "./api";
import { useQuarter } from "./quarter";

const REFRESH_MS = 60_000;

/** Items for the selected quarter's courses. Waits for the quarter list, so no other quarter flashes up. */
export function useItems(q: ItemQuery) {
  const { selected } = useQuarter();
  const query = { ...q, quarter: selected ?? undefined };
  return useQuery({
    queryKey: ["items", itemParams(query).toString()],
    queryFn: () => api.items(query),
    refetchInterval: REFRESH_MS,
    enabled: selected !== null,
  });
}

/** Courses that currently have items, for pickers and course cards. */
export function useCourses() {
  const { selected, isCurrent } = useQuarter();
  return useQuery({
    // This quarter's: only courses current sources still see. Another quarter's: all of them.
    queryKey: ["courses", isCurrent, selected],
    queryFn: () => api.courses(isCurrent, selected),
    refetchInterval: 5 * REFRESH_MS,
    enabled: selected !== null,
  });
}

/** Every course in any quarter, for labeling things that may be from another quarter. */
export function useAllCourses() {
  return useQuery({ queryKey: ["courses", "all"], queryFn: () => api.courses(false), refetchInterval: 5 * REFRESH_MS });
}

/**
 * Change your overlay on an item (done, hidden, pinned, note). Every list showing the item
 * updates immediately; if the server refuses, they roll back.
 */
export function useOverlay() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: OverlayPatch }) => api.patchOverlay(id, patch),
    onMutate: async ({ id, patch }) => {
      await client.cancelQueries({ queryKey: ["items"] });
      const previous = client.getQueriesData<Item[]>({ queryKey: ["items"] });
      const applied = { ...patch, ...(patch.note !== undefined && { note: patch.note.trim() || null }) };
      client.setQueriesData<Item[]>({ queryKey: ["items"] }, (items) =>
        items?.map((i) => (i.id === id ? { ...i, ...applied } : i)),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => context?.previous.forEach(([key, data]) => client.setQueryData(key, data)),
    onSettled: (_data, _error, { patch }) => {
      // Splitting a merged item changes how many items there are, and grades count items.
      if (patch.separate !== undefined) client.invalidateQueries({ queryKey: ["grades"] });
      return client.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

export function useGrades() {
  const { selected } = useQuarter();
  return useQuery({
    queryKey: ["grades", selected],
    queryFn: () => api.grades(selected),
    refetchInterval: 5 * REFRESH_MS,
    enabled: selected !== null,
  });
}

/** An element's current width, for components that lay out differently when narrow. */
export function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

export function useSources() {
  return useQuery({ queryKey: ["sources"], queryFn: api.sources, refetchInterval: REFRESH_MS });
}

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: Infinity });
}

/** The dashboard's timezone (from config.yaml), falling back to the device's until it loads. */
export function useTimezone(): string {
  return useMeta().data?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** Current time, re-rendering every `everyMs` so countdowns stay live. */
export function useNow(everyMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}

/** `now` rounded down to the hour: for query windows, so they don't refetch on every tick. */
export function hourFloor(now: number): number {
  return Math.floor(now / 3_600_000) * 3_600_000;
}
