import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { api, type QuarterInfo } from "./api";

// Which quarter the dashboard shows. It opens on the current one; picking another lasts for this tab
// (sessionStorage), so tomorrow's visit isn't stuck on last spring.

const KEY = "quarter";

interface QuarterState {
  selected: string | null; // null until the list loads
  current: string | null;
  quarters: QuarterInfo[];
  isCurrent: boolean;
  select: (code: string) => void;
}

const Context = createContext<QuarterState>({ selected: null, current: null, quarters: [], isCurrent: true, select: () => {} });

function stored(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function QuarterProvider({ children }: { children: ReactNode }) {
  const query = useQuery({ queryKey: ["quarters"], queryFn: api.quarters, staleTime: 5 * 60_000, refetchInterval: 15 * 60_000 });
  const [chosen, setChosen] = useState<string | null>(stored);
  const current = query.data?.current ?? null;
  const quarters = query.data?.quarters ?? [];
  const selected = chosen && quarters.some((q) => q.code === chosen) ? chosen : current;

  const select = useCallback(
    (code: string) => {
      const next = code === current ? null : code;
      setChosen(next);
      try {
        if (next) sessionStorage.setItem(KEY, next);
        else sessionStorage.removeItem(KEY);
      } catch {
        // storage blocked: the choice lasts until reload
      }
    },
    [current],
  );

  return (
    <Context.Provider value={{ selected, current, quarters, isCurrent: selected === current, select }}>
      {children}
    </Context.Provider>
  );
}

export function useQuarter(): QuarterState {
  return useContext(Context);
}
