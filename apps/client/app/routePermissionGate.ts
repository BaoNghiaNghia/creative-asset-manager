import { useEffect, useState } from "react";
import { AccessApiError, fetchAccessIdentity } from "../features/access_management";

const reloadAfterMaintenance = () => window.location.reload();

export const ROUTE_IDENTITY_TIMEOUT_MS = 5_000;
export const ROUTE_IDENTITY_RETRY_MS = 5_000;
export type RouteAccessState =
  | { status: "checking" }
  | { status: "allowed"; permissions: readonly string[] }
  | { status: "denied" }
  | { status: "maintenance" };

function unavailable(error: unknown): boolean {
  // Never disguise explicit authorization failures as maintenance.
  if (error instanceof AccessApiError) {
    return error.status >= 500 || error.status === 0;
  }
  return true; // network failures, AbortError, and browser transport errors
}

/** API downtime is a temporary maintenance screen, not an authorization denial.
 * A successful recovery reloads the exact original URL so stale lazy chunks
 * from a previous frontend release are never used after deployment.
 */
export function useRouteAccessGate(
  requiredPermission: string | null,
  onRecovered: () => void = reloadAfterMaintenance,
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
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let requestTimeout: ReturnType<typeof setTimeout> | undefined;
    let abort: AbortController | undefined;

    setState({ status: "checking" });
    async function checkIdentity() {
      abort = new AbortController();
      const scopedFetch: typeof fetch = (input, init) =>
        fetch(input, { ...init, signal: abort?.signal });
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
          setState({ status: "denied" });
          return;
        }
        hasEnteredMaintenance = true;
        setState({ status: "maintenance" });
        retryTimer = setTimeout(() => { if (active) void checkIdentity(); }, ROUTE_IDENTITY_RETRY_MS);
      } finally {
        if (requestTimeout !== undefined) clearTimeout(requestTimeout);
      }
    }

    void checkIdentity();
    return () => {
      active = false;
      abort?.abort();
      if (requestTimeout !== undefined) clearTimeout(requestTimeout);
      if (retryTimer !== undefined) clearTimeout(retryTimer);
    };
  }, [requiredPermission, onRecovered]);

  return state;
}
