import { describe, expect, it, vi } from "vitest";
import { fetchAiOperationsScope, filtersFromSearch, type DashboardFieldProgress } from "./api";

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AI Operations progressive scope loading", () => {
  it("publishes a fast field before a slow field without waiting for the whole group", async () => {
    let resolveSummary!: (value: Response) => void;
    let resolveProviders!: (value: Response) => void;
    const events: DashboardFieldProgress[] = [];
    const fetcher = vi.fn((url: RequestInfo | URL) => {
      const path = String(url);
      if (path.includes("/summary?")) {
        return new Promise<Response>(resolve => { resolveSummary = resolve; });
      }
      if (path.includes("/providers?")) {
        return new Promise<Response>(resolve => { resolveProviders = resolve; });
      }
      if (path.includes("/daily?")) return Promise.resolve(json({ items: [{ date: "2026-11-13" }] }));
      return Promise.resolve(json({}));
    }) as unknown as typeof fetch;

    const done = fetchAiOperationsScope(
      filtersFromSearch(""), ["summary", "daily", "providers"],
      fetcher, new Date("2026-11-13T08:00:00Z"), undefined,
      progress => events.push(progress),
    );
    await vi.waitFor(() => expect(events.some(event => event.field === "daily")).toBe(true));
    expect(events.find(event => event.field === "daily")?.partial.data.daily).toHaveLength(1);
    expect(events.some(event => event.field === "summary")).toBe(false);
    expect(events.some(event => event.field === "providers")).toBe(false);

    resolveSummary(json(null));
    resolveProviders(json({ items: [] }));
    const result = await done;
    expect(result.errors).toHaveLength(0);
    expect(result.data.daily).toHaveLength(1);
    expect(events.map(event => event.field)).toHaveLength(3);
    expect(events.map(event => event.completed).sort((a, b) => a - b)).toEqual([1, 2, 3]);
  });

  it("never exceeds three concurrent requests even when more fields are streamed", async () => {
    let inFlight = 0;
    let maximum = 0;
    const releases: Array<() => void> = [];
    const fetcher = vi.fn((url: RequestInfo | URL) => {
      inFlight += 1;
      maximum = Math.max(maximum, inFlight);
      return new Promise<Response>(resolve => {
        releases.push(() => {
          inFlight -= 1;
          resolve(json(String(url).includes("/summary?") ? null : { items: [] }));
        });
      });
    }) as unknown as typeof fetch;

    const done = fetchAiOperationsScope(
      filtersFromSearch(""), ["summary", "daily", "providers", "failures"],
      fetcher, new Date("2026-11-13T08:00:00Z"),
    );
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(maximum).toBe(3);
    releases.shift()?.();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
    while (releases.length) releases.shift()?.();
    const result = await done;
    expect(maximum).toBe(3);
    expect(result.errors).toEqual([]);
  });

  it("does not publish stale progress after a user switches tabs and aborts the scope", async () => {
    const controller = new AbortController();
    const events: DashboardFieldProgress[] = [];
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn((_url: RequestInfo | URL) =>
      new Promise<Response>(done => { resolve = done; })) as unknown as typeof fetch;
    const done = fetchAiOperationsScope(
      filtersFromSearch(""), ["summary"], fetcher,
      new Date("2026-11-13T08:00:00Z"), controller.signal,
      event => events.push(event),
    );
    controller.abort();
    resolve(json(null));
    await done;
    expect(events).toEqual([]);
  });
});
