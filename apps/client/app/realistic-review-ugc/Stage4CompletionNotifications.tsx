import { useCallback, useEffect, useRef, useState } from "react";
import { showActionToast } from "../components/ActionToast";
import { listStage4Completions } from "./api";
import type { Stage4CompletionEvent } from "./types";
import "./Stage4CompletionNotifications.css";

const POLL_INTERVAL_MS = 15_000;
const MAX_PAGES_PER_POLL = 5;
const CURSOR_KEY = "cam:rrugc:stage4:completion-cursor:";
const DESKTOP_KEY = "cam:rrugc:stage4:desktop-alerts:";
const TOAST_KEY = "cam:rrugc:stage4:toast-alerts:";

export function stage4CompletionMessage(items: readonly Stage4CompletionEvent[]): string {
  if (items.length === 1) return "Stage 4 completed: " + items[0].source_name;
  return items.length + " Stage 4 images completed.";
}

function saved(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

function store(key: string, value: string): void {
  try { window.localStorage.setItem(key, value); } catch { /* storage blocked */ }
}

function afterCursor(candidate: string, baseline: string): boolean {
  if (!candidate || !baseline || baseline === "start") return Boolean(candidate);
  if (candidate === "start") return false;
  // Feed cursors are ISO timestamps + "|" + UUID. Lexically sortable
  // because the API uses a consistent timestamp representation.
  return candidate > baseline;
}

type Props = { onViewStage4: () => void };

export function Stage4CompletionNotifications({ onViewStage4 }: Props) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState("");
  const [unread, setUnread] = useState(0);
  const [lastSummary, setLastSummary] = useState("");
  const [desktopEnabled, setDesktopEnabled] = useState(false);
  const [toastEnabled, setToastEnabled] = useState(true);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );
  const [feedback, setFeedback] = useState("");
  const cursorRef = useRef<string | undefined>(undefined);
  const scopeRef = useRef("");
  const inFlight = useRef(false);
  const pendingVisible = useRef<Stage4CompletionEvent[]>([]);
  const jump = useCallback(() => {
    setUnread(0);
    setOpen(false);
    onViewStage4();
  }, [onViewStage4]);
  const jumpRef = useRef(jump);
  useEffect(() => { jumpRef.current = jump; }, [jump]);

  useEffect(() => {
    const aborter = new AbortController();
    let alive = true;

    async function poll() {
      if (!alive || inFlight.current) return;
      inFlight.current = true;
      try {
        if (cursorRef.current === undefined) {
          const bootstrap = await listStage4Completions(undefined, aborter.signal);
          if (!alive) return;
          const key = CURSOR_KEY + encodeURIComponent(bootstrap.scope);
          scopeRef.current = bootstrap.scope;
          setScope(bootstrap.scope);
          setDesktopEnabled(saved(DESKTOP_KEY + encodeURIComponent(bootstrap.scope)) === "1");
          setToastEnabled(saved(TOAST_KEY + encodeURIComponent(bootstrap.scope)) !== "0");
          const previous = saved(key);
          cursorRef.current = previous || bootstrap.cursor;
          if (!previous) {
            store(key, bootstrap.cursor);
            return; // Do not announce the full archive at initial install.
          }
        }

        const identity = encodeURIComponent(scopeRef.current);
        const key = CURSOR_KEY + identity;
        const currentStored = saved(key);
        if (currentStored && afterCursor(currentStored, cursorRef.current || "start")) {
          cursorRef.current = currentStored;
        }
        const items: Stage4CompletionEvent[] = [];
        let nextCursor = cursorRef.current;
        for (let page = 0; page < MAX_PAGES_PER_POLL; page += 1) {
          const feed = await listStage4Completions(nextCursor, aborter.signal);
          if (!alive || feed.scope !== scopeRef.current) return;
          if (feed.items.length) items.push(...feed.items);
          if (feed.cursor === nextCursor) break;
          nextCursor = feed.cursor;
          if (!feed.has_more) break;
        }
        if (!alive) return;
        // Commit only after the whole fetch batch succeeds. Failed/aborted
        // requests cannot make completion notifications disappear.
        if (nextCursor !== cursorRef.current) {
          cursorRef.current = nextCursor;
          store(key, nextCursor || "start");
        }
        if (!items.length) return;
        const message = stage4CompletionMessage(items);
        setLastSummary(message);
        setUnread(current => current + items.length);

        if (saved(TOAST_KEY + identity) !== "0") {
          if (!document.hidden) {
            showActionToast(message, "success", { label: "View Stage 4", onClick: () => jumpRef.current() });
          } else {
            pendingVisible.current.push(...items);
          }
        }
        if (document.hidden && saved(DESKTOP_KEY + identity) === "1"
          && typeof Notification !== "undefined" && Notification.permission === "granted") {
          try {
            const notice = new Notification("CAM · Stage 4 images ready", {
              body: message + " Click to view output.",
              icon: "/favicon.svg",
              tag: "cam-stage4-complete",
            });
            notice.onclick = () => {
              window.focus();
              jumpRef.current();
              notice.close();
            };
          } catch {
            // Browser/OS notification is best effort. Cursor and unread
            // counter are already persisted; do not replay or spam.
          }
        }
      } catch (error) {
        if (alive && !(error instanceof DOMException && error.name === "AbortError")) {
          // Keep cursor unchanged. Retry next cycle without losing events.
        }
      } finally {
        inFlight.current = false;
      }
    }

    void poll();
    const timer = window.setInterval(() => { void poll(); }, POLL_INTERVAL_MS);
    const whenActive = () => {
      if (!document.hidden && pendingVisible.current.length) {
        const pending = pendingVisible.current.splice(0);
        if (saved(TOAST_KEY + encodeURIComponent(scopeRef.current)) !== "0") {
          showActionToast(stage4CompletionMessage(pending), "success", {
            label: "View Stage 4", onClick: () => jumpRef.current(),
          });
        }
      }
      void poll();
    };
    window.addEventListener("focus", whenActive);
    document.addEventListener("visibilitychange", whenActive);
    return () => {
      alive = false;
      aborter.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", whenActive);
      document.removeEventListener("visibilitychange", whenActive);
    };
  }, []);

  async function toggleDesktop() {
    if (!scope || typeof Notification === "undefined") {
      setFeedback("Desktop alerts are not supported in this browser.");
      return;
    }
    if (desktopEnabled) {
      store(DESKTOP_KEY + encodeURIComponent(scope), "0");
      setDesktopEnabled(false);
      setFeedback("Desktop alerts disabled.");
      return;
    }
    try {
      // Permission is requested only from this direct button interaction.
      const allowed = await Notification.requestPermission();
      setPermission(allowed);
      if (allowed === "granted") {
        store(DESKTOP_KEY + encodeURIComponent(scope), "1");
        setDesktopEnabled(true);
        setFeedback("Desktop alerts enabled while CAM is open.");
      } else {
        setFeedback("Browser notification permission was not granted.");
      }
    } catch {
      setFeedback("Unable to enable notifications in this browser.");
    }
  }

  function toggleToast(checked: boolean) {
    if (!scope) return;
    store(TOAST_KEY + encodeURIComponent(scope), checked ? "1" : "0");
    setToastEnabled(checked);
  }

  return <div className="rrugc-stage4-notify">
    <button type="button" className="rrugc-global-management-button rrugc-stage4-notify-trigger"
      aria-expanded={open} aria-label={"Stage 4 completion notifications" + (unread ? ", " + unread + " new" : "")}
      onClick={() => setOpen(value => !value)}>
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>
      </svg>
      Stage 4 alerts {unread > 0 && <span className="rrugc-stage4-notify-count">{unread > 99 ? "99+" : unread}</span>}
    </button>
    {open && <section className="rrugc-stage4-notify-panel" aria-label="Stage 4 notification preferences">
      <strong>Stage 4 · Image completion</strong>
      <p>Check every 15 seconds while CAM is open. Combine completed images into a single notice.</p>
      <label className="rrugc-stage4-notify-checkbox">
        <input type="checkbox" checked={toastEnabled} disabled={!scope}
          onChange={event => toggleToast(event.target.checked)}/>
        Toast in CAM
      </label>
      <button type="button" className="rrugc-stage4-notify-desktop"
        onClick={() => void toggleDesktop()} disabled={!scope || permission === "unsupported" || (permission === "denied" && !desktopEnabled)}>
        {desktopEnabled ? "Disable desktop notifications" : "Enable desktop notifications"}
      </button>
      {permission === "denied" && <small>Browser permission is blocked. Change it in site settings.</small>}
      <small>Desktop alerts work with CAM open in a browser tab, even in the background. They are not push notifications when the browser is closed.</small>
      {lastSummary && <p role="status">{lastSummary}</p>}
      {feedback && <small role="status">{feedback}</small>}
      <button type="button" className="rrugc-stage4-notify-view" onClick={jump}>View Stage 4 outputs</button>
    </section>}
  </div>;
}
