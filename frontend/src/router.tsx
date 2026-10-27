import { useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from "react";

// Two pages (the board at /, tutoring at /tutor) don't need a router library: the path is the state.
// The server answers page addresses with index.html (backend/app/main.py: WebApp).

const CHANGE = "pathchange";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

export function navigate(to: string, { replace = false } = {}): void {
  if (to === window.location.pathname) return;
  if (replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  window.dispatchEvent(new Event(CHANGE));
  window.scrollTo(0, 0);
}

/** A link within the app: changes the page without reloading. New-tab clicks work as usual. */
export function Link({ to, onClick, ...props }: { to: string } & AnchorHTMLAttributes<HTMLAnchorElement>) {
  const go = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={go} {...props} />;
}
