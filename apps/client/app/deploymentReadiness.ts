import { useEffect, useState } from "react";

export const BACKEND_READINESS_POLL_MS = 15_000;
export const BACKEND_READINESS_TIMEOUT_MS = 4_000;
export const BACKEND_READINESS_INITIAL_DELAY_MS = 2_000;
export const BACKEND_READINESS_CONFIRM_RETRY_MS = 3_000;

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
    let consecutiveFailures = 0;
    let running = false;
    let confirmationTimer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let watchdog: ReturnType<typeof setTimeout> | undefined;

    const probe = async () => {
      if (!alive || running) return;
      running = true;
      controller = new AbortController();
      watchdog = setTimeout(() => controller?.abort(), BACKEND_READINESS_TIMEOUT_MS);
      let backendReportedUnavailable = false;
      try {
        const response = await fetch("/ready", {
          method: "GET", cache: "no-store", credentials: "same-origin",
          signal: controller.signal,
        });
        // Only backend failures signal deployment downtime. An unexpected
        // 4xx from an intermediary is not evidence of a server shutdown.
        if (response.status >= 500) {
          backendReportedUnavailable = true;
          throw new Error("backend_not_ready");
        }
        if (!response.ok) {
          // Authorization/proxy 4xx responses are not evidence of an outage.
          consecutiveFailures = 0;
          return;
        }
        if (!alive) return;
        consecutiveFailures = 0;
        if (confirmationTimer !== undefined) clearTimeout(confirmationTimer);
        if (outage) {
          onRecovered();
          return;
        }
        setUnavailable(false);
      } catch {
        if (!alive) return;
        if (!backendReportedUnavailable) {
          // Network errors and request timeouts are not proof of a server
          // outage. Keep already-open workspaces visible and try again later.
          consecutiveFailures = 0;
          return;
        }
        consecutiveFailures += 1;
        // A single slow/failed probe during a deploy should not replace the
        // whole workspace with the Under Construction screen.
        if (consecutiveFailures < 2) {
          confirmationTimer = setTimeout(() => void probe(), BACKEND_READINESS_CONFIRM_RETRY_MS);
          return;
        }
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
      if (confirmationTimer !== undefined) clearTimeout(confirmationTimer);
      if (watchdog !== undefined) clearTimeout(watchdog);
    };
  }, [enabled, onRecovered]);

  return unavailable;
}
