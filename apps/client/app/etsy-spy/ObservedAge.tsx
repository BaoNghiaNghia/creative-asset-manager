import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { readableTime } from "./api";

/** Use elapsed time, not the local calendar date, so 24h and 7d are exact. */
export function formatObservedAge(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "—";
  const elapsed = now - timestamp;
  if (elapsed < -60_000) return "Chưa đến";
  const minutes = Math.max(0, Math.floor(elapsed / 60_000));
  if (minutes === 0) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} tiếng trước`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} ngày trước`;
  return `${Math.floor(days / 7)} tuần trước`;
}

// One lightweight clock per table. Only time labels consume this context;
// no extra API requests, row resets, or per-listing intervals.
const ObservedClockContext = createContext<number | null>(null);
export function ObservedClock({ children }: { children: ReactNode }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const interval = window.setInterval(tick, 60_000);
    const onVisible = () => { if (!document.hidden) tick(); };
    window.addEventListener("focus", tick);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", tick);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
  return <ObservedClockContext.Provider value={now}>{children}</ObservedClockContext.Provider>;
}

export function ObservedAge({ value }: { value: string | null | undefined }) {
  const now = useContext(ObservedClockContext);
  const valid = Boolean(value && Number.isFinite(Date.parse(value)));
  if (!valid) return <span className="etsy-observed-age is-missing">—</span>;
  const absolute = readableTime(value) + " (GMT+7)";
  return <time className="etsy-observed-age" dateTime={value!} title={absolute}>
    {formatObservedAge(value, now ?? Date.now())}
  </time>;
}
