// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BACKEND_READINESS_INITIAL_DELAY_MS,
  BACKEND_READINESS_POLL_MS,
  BACKEND_READINESS_TIMEOUT_MS,
  useDeploymentReadiness,
} from "./deploymentReadiness";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
let host: HTMLDivElement | null = null;
function Probe({ enabled = true, recovered }: { enabled?: boolean; recovered: () => void }) {
  const outage = useDeploymentReadiness(enabled, recovered);
  return <output>{outage ? "maintenance" : "operational"}</output>;
}
async function mount(recovered = vi.fn(), enabled = true) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<Probe enabled={enabled} recovered={recovered} />); });
  return { recovered, get status() { return host?.querySelector("output")?.textContent; } };
}
afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  host?.remove();
  host = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("deployment watcher for already-open workspaces", () => {
  it("switches to maintenance on failed readiness, then reloads the existing URL when restored", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValueOnce(new Response("updating", { status: 503 }))
      .mockResolvedValueOnce(new Response("ready", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const recovered = vi.fn();
    const probe = await mount(recovered);
    expect(probe.status).toBe("operational");
    await act(async () => { await vi.advanceTimersByTimeAsync(BACKEND_READINESS_INITIAL_DELAY_MS); });
    expect(probe.status).toBe("maintenance");
    await act(async () => { await vi.advanceTimersByTimeAsync(BACKEND_READINESS_POLL_MS); });
    expect(recovered).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith("/ready", expect.objectContaining({cache: "no-store"}));
  });

  it("treats hung health requests as downtime without permanently blocking the browser", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      }));
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKEND_READINESS_INITIAL_DELAY_MS + BACKEND_READINESS_TIMEOUT_MS);
    });
    expect(probe.status).toBe("maintenance");
  });

  it("does not mistake an intermediary 400 for a maintenance outage", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue(new Response("invalid request", {status: 400}));
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(BACKEND_READINESS_INITIAL_DELAY_MS); });
    expect(probe.status).toBe("operational");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("never polls backend for intentionally public pages", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const probe = await mount(vi.fn(), false);
    await act(async () => { await vi.advanceTimersByTimeAsync(BACKEND_READINESS_POLL_MS * 3); });
    expect(probe.status).toBe("operational");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
