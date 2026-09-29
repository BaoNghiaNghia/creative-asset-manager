import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

const MIN_WIDTH = 220;
const MAX_WIDTH = 480;
const DEFAULT_WIDTH = 256;
const COMPACT_QUERY = "(max-width: 1024px)";

function compactViewport(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(COMPACT_QUERY).matches;
}

export function useResizableSidebar() {
  const [width, setWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem("cam-sidebar-width"));
    return Number.isFinite(saved) && saved >= MIN_WIDTH && saved <= MAX_WIDTH ? saved : DEFAULT_WIDTH;
  });
  const [collapsed, setCollapsed] = useState(
    () => compactViewport() || window.localStorage.getItem("cam-sidebar-collapsed") === "true",
  );
  const resizing = useRef(false);

  useEffect(() => {
    function resize(event: PointerEvent) {
      if (!resizing.current) return;
      const nextWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, event.clientX));
      setWidth(nextWidth);
      window.localStorage.setItem("cam-sidebar-width", String(nextWidth));
    }

    function stop() {
      if (!resizing.current) return;
      resizing.current = false;
      document.body.classList.remove("resizing-sidebar");
    }

    window.addEventListener("pointermove", resize);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", resize);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, []);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(COMPACT_QUERY);
    const handleCompactChange = (event: MediaQueryListEvent) => {
      if (event.matches) setCollapsed(true);
    };
    media.addEventListener?.("change", handleCompactChange);
    return () => media.removeEventListener?.("change", handleCompactChange);
  }, []);

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    resizing.current = true;
    document.body.classList.add("resizing-sidebar");
  }

  function setVisibility(nextCollapsed: boolean) {
    setCollapsed(nextCollapsed);
    window.localStorage.setItem("cam-sidebar-collapsed", String(nextCollapsed));
  }

  return {
    width,
    collapsed,
    startResize,
    collapse: () => setVisibility(true),
    restore: () => setVisibility(false),
  };
}
