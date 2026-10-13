// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ROUTE_IDENTITY_RETRY_MS, ROUTE_IDENTITY_TIMEOUT_MS, useRouteAccessGate } from "./routePermissionGate";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let currentRoot: Root | null = null;
let host: HTMLDivElement | null = null;

function Probe({ permission = "inventory.read", recovered }: { permission?: string | null; recovered: () => void }) {
  const access = useRouteAccessGate(permission, recovered);
  return <output data-testid="gate">{access.status}</output>;
}

async function mount(recovered = vi.fn(), permission: string | null = "inventory.read") {
  host = document.createElement("div");
  document.body.appendChild(host);
  currentRoot = createRoot(host);
  await act(async () => {
    currentRoot!.render(<Probe permission={permission} recovered={recovered} />);
    await Promise.resolve();
  });
  return { recovered, get status() { return host?.querySelector("output")?.textContent; } };
}
afterEach(async () => {
  if (currentRoot) await act(async () => currentRoot?.unmount());
  currentRoot = null;
  host?.remove();
  host = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const identity = (permissions: string[] = ["inventory.read"]) =>
  new Response(JSON.stringify({ permissions }), { status: 200, headers: { "Content-Type": "application/json" } });

describe("production deploy permissions gate", () => {
  it("shows maintenance on 503 and reloads the original route once identity recovers", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValueOnce(new Response("updating", { status: 503 }))
      .mockResolvedValueOnce(identity());
    vi.stubGlobal("fetch", fetcher);
    const recovered = vi.fn();
    const probe = await mount(recovered);
    expect(probe.status).toBe("maintenance");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_RETRY_MS); });
    expect(recovered).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("never hangs on Checking permissions when backend stops responding", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount();
    expect(probe.status).toBe("checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_TIMEOUT_MS); });
    expect(probe.status).toBe("maintenance");
  });

  it("keeps authorization failures separate from production downtime", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: "authentication_required", message: "Login required" } }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount();
    expect(probe.status).toBe("denied");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_RETRY_MS); });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("denies an authenticated account missing permission rather than showing maintenance", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(identity(["assets.read"])));
    const probe = await mount();
    expect(probe.status).toBe("denied");
  });

  it("does not check backend identity for public or unauthenticated routes", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount(vi.fn(), null);
    expect(probe.status).toBe("allowed");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
