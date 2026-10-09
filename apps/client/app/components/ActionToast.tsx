import { useEffect, useRef, useState } from "react";
import "./ActionToast.css";

export type ActionToastTone = "success" | "error" | "info" | "warning";
export const ACTION_TOAST_DURATION_MS = 5_000;
const EVENT_NAME = "cam:action-toast";
const MAX_VISIBLE = 3;

type ToastNotice = { id: number; message: string; tone: ActionToastTone; expiresAt: number };
type ToastPayload = { message: string; tone: ActionToastTone };
let nextToastId = 0;

/** Shared feedback channel for user-triggered actions on any app route. */
export function showActionToast(message: string, tone: ActionToastTone = "success"): void {
  const text = message.trim();
  if (!text || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<ToastPayload>(EVENT_NAME, { detail: { message: text, tone } }));
}

/** Bridge older page-local action messages without retaining an inline banner. */
export function ActionMessageToast({
  message, tone = "success", nonce,
}: { message?: string | null; tone?: ActionToastTone; nonce?: unknown }) {
  const lastShown = useRef<{ message: string; tone: ActionToastTone; nonce: unknown } | null>(null);
  useEffect(() => {
    if (!message) {
      lastShown.current = null;
      return;
    }
    if (lastShown.current?.message === message && lastShown.current.tone === tone && Object.is(lastShown.current.nonce, nonce)) return;
    lastShown.current = { message, tone, nonce };
    showActionToast(message, tone);
  }, [message, tone, nonce]);
  return null;
}

const ICONS: Record<ActionToastTone, string> = {
  success: "✓", error: "!", warning: "!", info: "i",
};

export function ActionToastViewport() {
  const [notices, setNotices] = useState<ToastNotice[]>([]);
  useEffect(() => {
    const handle = (event: Event) => {
      const { message, tone } = (event as CustomEvent<ToastPayload>).detail;
      const id = ++nextToastId;
      setNotices(previous => [...previous, { id, message, tone, expiresAt: Date.now() + ACTION_TOAST_DURATION_MS }].slice(-MAX_VISIBLE));
    };
    window.addEventListener(EVENT_NAME, handle);
    return () => window.removeEventListener(EVENT_NAME, handle);
  }, []);

  useEffect(() => {
    if (!notices.length) return;
    const nextExpiry = Math.min(...notices.map(notice => notice.expiresAt));
    const timer = window.setTimeout(() => {
      setNotices(previous => previous.filter(notice => notice.expiresAt > Date.now()));
    }, Math.max(0, nextExpiry - Date.now()));
    return () => window.clearTimeout(timer);
  }, [notices]);

  return <div className="cam-action-toasts" aria-label="Action notifications">
    {notices.map(notice =>
      <div key={notice.id} className={`cam-action-toast cam-action-toast--${notice.tone}`}
        role={notice.tone === "error" ? "alert" : "status"} aria-live={notice.tone === "error" ? "assertive" : "polite"}>
        <span className="cam-action-toast-icon" aria-hidden="true">{ICONS[notice.tone]}</span>
        <span className="cam-action-toast-message">{notice.message}</span>
        <button type="button" onClick={() => setNotices(previous => previous.filter(item => item.id !== notice.id))}
          aria-label="Dismiss notification" title="Dismiss">×</button>
      </div>
    )}
  </div>;
}
