import { useEffect, useRef } from "react";

/**
 * Keep the selected tab visible inside its own horizontal navigation rail.
 * Unlike scrollIntoView, this never scrolls the whole document or closes a modal.
 * It intentionally makes no changes to focus, selected stage, or browser history.
 */
export function useActiveStageVisibility(activeStage: string) {
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const active = nav.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
    if (!active) return;

    const viewport = nav.getBoundingClientRect();
    const tab = active.getBoundingClientRect();
    const inset = 12;
    let offset = 0;
    if (tab.left < viewport.left + inset) offset = tab.left - viewport.left - inset;
    else if (tab.right > viewport.right - inset) offset = tab.right - viewport.right + inset;

    if (Math.abs(offset) > 1) nav.scrollTo?.({ left: nav.scrollLeft + offset, behavior: "auto" });
  }, [activeStage]);

  return navRef;
}
