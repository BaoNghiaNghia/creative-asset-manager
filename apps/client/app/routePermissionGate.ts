import { useEffect, useState } from "react";
import { AccessApiError, fetchAccessIdentity } from "../features/access_management";

const reloadAfterMaintenance = () => window.location.reload();

export const ROUTE_IDENTITY_TIMEOUT_MS = 10_000;
export const ROUTE_IDENTITY_RETRY_MS = 5_000;
export const ROUTE_IDENTITY_CONFIRM_RETRY_MS = 1_500;
export const ROUTE_READINESS_TIMEOUT_MS = 3_000;
export type RouteAccessState =
  | { status: "checking" }
  | { status: "allowed"; permissions: readonly string[] }
  | { status: "denied" }
  | { status: "unauthenticated" }
  | { status: "reconnecting" }
  | { status: "maintenance" };

function unavailable(error: unknown): boolean {
  // Never disguise explicit authorization failures as maintenance.
  if (error instanceof AccessApiError) {
    return error.status >= 500 || error.status === 0 || error.status === 429;
  }
  return true; // network failures, AbortError, and browser transport errors
}

/** An identity failure is not sufficient evidence for maintenance: the API
 * might be healthy while the user's connection or the auth endpoint is slow.
 * Only an explicit 5xx response from /ready confirms server downtime.
 */
async function backendConfirmsMaintenance(): Promise<boolean> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const response = await Promise.race([
      fetch("/ready", { method: "GET", cache: "no-store", credentials: "same-origin", signal: controller.signal }),
      new Promise<null>(resolve => {
        timer = setTimeout(() => { controller.abort(); resolve(null); }, ROUTE_READINESS_TIMEOUT_MS);
      }),
    ]);
    return response !== null && response.status >= 500;
  } catch {
    return false;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

/** Confirmed API downtime is a temporary maintenance screen, not an authorization denial.
 * A successful recovery reloads the exact original URL so stale lazy chunks
 * from a previous frontend release are never used after deployment.
 */
export function useRouteAccessGate(
  requiredPermission: string | null,
  onRecovered: () => void = reloadAfterMaintenance,
  retryKey = 0,
): RouteAccessState {
  const [state, setState] = useState<RouteAccessState>(
    requiredPermission ? { status: "checking" } : { status: "allowed", permissions: [] },
  );

  useEffect(() => {
    if (!requiredPermission) {
      setState({ status: "allowed", permissions: [] });
      return;
    }

    let active = true;
    let hasEnteredMaintenance = false;
    let consecutiveFailures = 0;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let abort: AbortController | undefined;

    setState({ status: "checking" });
    async function checkIdentity() {
      const currentAbort = new AbortController();
      abort = currentAbort;
      let requestTimeout: ReturnType<typeof setTimeout> | undefined;
      const scopedFetch: typeof fetch = (input, init) =>
        fetch(input, { ...init, signal: currentAbort.signal });
      try {
        // Race the request: even a non-cooperative transport can't leave the
        // application on "Checking permissions…" indefinitely.
        const timeout = new Promise<never>((_resolve, reject) => {
          requestTimeout = setTimeout(() => {
            abort?.abort();
            reject(new Error("workspace_identity_timeout"));
          }, ROUTE_IDENTITY_TIMEOUT_MS);
        });
        const identity = await Promise.race([fetchAccessIdentity(scopedFetch), timeout]);
        if (!active) return;
        consecutiveFailures = 0;
        if (hasEnteredMaintenance) {
          onRecovered();
          return;
        }
        setState(identity.permissions.includes(requiredPermission!)
          ? { status: "allowed", permissions: identity.permissions }
          : { status: "denied" });
      } catch (error) {
        if (!active) return;
        if (!unavailable(error)) {
          setState({ status: error instanceof AccessApiError && error.status === 401 ? "unauthenticated" : "denied" });
          return;
        }
        consecutiveFailures += 1;
        if (consecutiveFailures < 2) {
          // One transient 5xx or timeout during deployment is not a durable
          // maintenance state. Keep the permission loading view briefly.
          retryTimer = setTimeout(() => { if (active) void checkIdentity(); }, ROUTE_IDENTITY_CONFIRM_RETRY_MS);
          return;
        }
        const maintenanceConfirmed = await backendConfirmsMaintenance();
        if (!active) return;
        if (maintenanceConfirmed) {
          hasEnteredMaintenance = true;
          setState({ status: "maintenance" });
        } else {
          // Keep the protected route closed while retrying authentication;
          // never show Under Construction for a healthy backend.
          setState({ status: "reconnecting" });
        }
        retryTimer = setTimeout(() => { if (active) void checkIdentity(); }, ROUTE_IDENTITY_RETRY_MS);
      } finally {
        if (requestTimeout !== undefined) clearTimeout(requestTimeout);
      }
    }

    void checkIdentity();
    return () => {
      active = false;
      abort?.abort();
      if (retryTimer !== undefined) clearTimeout(retryTimer);
    };
  }, [requiredPermission, onRecovered, retryKey]);

  return state;
}
