// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ROUTE_IDENTITY_CONFIRM_RETRY_MS, ROUTE_IDENTITY_RETRY_MS, ROUTE_IDENTITY_TIMEOUT_MS, ROUTE_READINESS_TIMEOUT_MS, useRouteAccessGate } from "./routePermissionGate";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let currentRoot: Root | null = null;
let host: HTMLDivElement | null = null;

function Probe({ permission = "inventory.read", recovered, retryKey = 0 }: { permission?: string | null; recovered: () => void; retryKey?: number }) {
  const access = useRouteAccessGate(permission, recovered, retryKey);
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
      .mockResolvedValueOnce(new Response("still updating", { status: 503 }))
      .mockResolvedValueOnce(new Response("backend not ready", { status: 503 }))
      .mockResolvedValueOnce(identity());
    vi.stubGlobal("fetch", fetcher);
    const recovered = vi.fn();
    const probe = await mount(recovered);
    expect(probe.status).toBe("checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_CONFIRM_RETRY_MS); });
    expect(probe.status).toBe("maintenance");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_RETRY_MS); });
    expect(recovered).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(fetcher).toHaveBeenCalledWith("/ready", expect.objectContaining({ cache: "no-store" }));
  });

  it("keeps Products Stage 2 out of maintenance when identity fails but /ready is healthy", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("auth busy", { status: 503 }))
      .mockResolvedValueOnce(new Response("auth busy again", { status: 503 }))
      .mockResolvedValueOnce(new Response('{"status":"ready"}', { status: 200 }))
      .mockResolvedValueOnce(identity(["realistic_review_ugc.read"]));
    vi.stubGlobal("fetch", fetcher);
    const recovered = vi.fn();
    const probe = await mount(recovered, "realistic_review_ugc.read");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_CONFIRM_RETRY_MS); });
    expect(probe.status).toBe("reconnecting");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_RETRY_MS); });
    expect(probe.status).toBe("allowed");
    expect(recovered).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledWith("/ready", expect.objectContaining({ cache: "no-store" }));
  });

  it("never hangs on Checking permissions when backend stops responding", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount();
    expect(probe.status).toBe("checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_TIMEOUT_MS); });
    expect(probe.status).toBe("checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_CONFIRM_RETRY_MS + ROUTE_IDENTITY_TIMEOUT_MS + ROUTE_READINESS_TIMEOUT_MS); });
    expect(probe.status).toBe("reconnecting");
  });

  it("retries a transient identity 503 without showing Under Construction", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("deploy blip", { status: 503 }))
      .mockResolvedValueOnce(identity());
    vi.stubGlobal("fetch", fetcher);
    const recovered = vi.fn();
    const probe = await mount(recovered);
    expect(probe.status).toBe("checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_CONFIRM_RETRY_MS); });
    expect(probe.status).toBe("allowed");
    expect(recovered).not.toHaveBeenCalled();
  });

  it("keeps authorization failures separate from production downtime", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: "authentication_required", message: "Login required" } }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount();
    expect(probe.status).toBe("unauthenticated");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_RETRY_MS); });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("recovers immediately on manual retry without reloading the page", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("auth temporarily busy", { status: 503 }))
      .mockResolvedValueOnce(new Response("still busy", { status: 503 }))
      .mockResolvedValueOnce(new Response('{"status":"ready"}', { status: 200 }))
      .mockResolvedValueOnce(identity(["realistic_review_ugc.read"]));
    vi.stubGlobal("fetch", fetcher);
    const recovered = vi.fn();
    const probe = await mount(recovered, "realistic_review_ugc.read");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_CONFIRM_RETRY_MS); });
    expect(probe.status).toBe("reconnecting");
    await act(async () => {
      currentRoot!.render(<Probe permission="realistic_review_ugc.read" recovered={recovered} retryKey={1} />);
      await Promise.resolve();
    });
    expect(probe.status).toBe("allowed");
    expect(recovered).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("retries temporary identity rate limits rather than showing access denied", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("busy", { status: 429 }))
      .mockResolvedValueOnce(identity()));
    const probe = await mount();
    expect(probe.status).toBe("checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(ROUTE_IDENTITY_CONFIRM_RETRY_MS); });
    expect(probe.status).toBe("allowed");
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
