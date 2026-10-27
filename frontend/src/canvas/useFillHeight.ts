import { useLayoutEffect, useRef, useState } from "react";

/** The rest of the window below the masthead: Excalidraw needs a definite height, and the page shouldn't scroll. */
export function useFillHeight() {
  const ref = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    const measure = () => {
      const top = ref.current ? ref.current.getBoundingClientRect().top + window.scrollY : 0;
      setHeight(Math.max(420, window.innerHeight - top - 16));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  return { ref, height };
}
