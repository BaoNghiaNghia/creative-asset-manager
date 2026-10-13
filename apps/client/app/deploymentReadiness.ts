import { useEffect, useState } from "react";

export const BACKEND_READINESS_POLL_MS = 15_000;
export const BACKEND_READINESS_TIMEOUT_MS = 4_000;
export const BACKEND_READINESS_INITIAL_DELAY_MS = 2_000;

const reloadCurrentRoute = () => window.location.reload();

/** Health watcher for workspaces that have *already* passed access gating.
 * Without this, a long-lived browser tab never observes deploy downtime
 * because the identity permission check only runs on initial navigation.
 */
export function useDeploymentReadiness(
  enabled: boolean,
  onRecovered: () => void = reloadCurrentRoute,
): boolean {
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setUnavailable(false);
      return;
    }
    let alive = true;
    let outage = false;
    let running = false;
    let controller: AbortController | null = null;
    let watchdog: ReturnType<typeof setTimeout> | undefined;

    const probe = async () => {
      if (!alive || running) return;
      running = true;
      controller = new AbortController();
      watchdog = setTimeout(() => controller?.abort(), BACKEND_READINESS_TIMEOUT_MS);
      try {
        const response = await fetch("/ready", {
          method: "GET", cache: "no-store", credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("backend_not_ready");
        if (!alive) return;
        if (outage) {
          onRecovered();
          return;
        }
        setUnavailable(false);
      } catch {
        if (!alive) return;
        outage = true;
        setUnavailable(true);
      } finally {
        if (watchdog !== undefined) clearTimeout(watchdog);
        running = false;
      }
    };
    const initial = setTimeout(() => void probe(), BACKEND_READINESS_INITIAL_DELAY_MS);
    const interval = setInterval(() => void probe(), BACKEND_READINESS_POLL_MS);
    return () => {
      alive = false;
      controller?.abort();
      clearTimeout(initial);
      clearInterval(interval);
      if (watchdog !== undefined) clearTimeout(watchdog);
    };
  }, [enabled, onRecovered]);

  return unavailable;
}
