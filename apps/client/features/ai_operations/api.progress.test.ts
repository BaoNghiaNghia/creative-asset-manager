import { describe, expect, it, vi } from "vitest";
import { fetchAiOperationsScope, filtersFromSearch, type DashboardFieldProgress } from "./api";

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AI Operations progressive scope loading", () => {
  it("does not overlap costly summary ranges and lets lightweight fields finish first", async () => {
    const started: string[] = [];
    const releases: Array<() => void> = [];
    let activeSummary = 0;
    let maximumSummary = 0;
    const fetcher = vi.fn((url: RequestInfo | URL) => {
      const pathname = new URL(String(url), "http://localhost").pathname;
      started.push(pathname);
      if (pathname.endsWith("/summary")) {
        activeSummary += 1;
        maximumSummary = Math.max(maximumSummary, activeSummary);
        return new Promise<Response>(resolve => {
          releases.push(() => {
            activeSummary -= 1;
            resolve(json(null));
          });
        });
      }
      return Promise.resolve(json({ items: [] }));
    }) as unknown as typeof fetch;

    const done = fetchAiOperationsScope(
      filtersFromSearch(""), ["summary", "today", "month", "daily", "providers", "failures"],
      fetcher, new Date("2026-11-13T10:00:00Z"),
    );
    await vi.waitFor(() => expect(started.filter(path => path.endsWith("/failures"))).toHaveLength(1));
    expect(started.filter(path => path.endsWith("/summary"))).toHaveLength(1);
    expect(started.some(path => path.endsWith("/daily"))).toBe(true);
    expect(started.some(path => path.endsWith("/providers"))).toBe(true);

    releases.shift()?.();
    await vi.waitFor(() => expect(started.filter(path => path.endsWith("/summary"))).toHaveLength(2));
    releases.shift()?.();
    await vi.waitFor(() => expect(started.filter(path => path.endsWith("/summary"))).toHaveLength(3));
    releases.shift()?.();

    const result = await done;
    expect(result.errors).toHaveLength(0);
    expect(maximumSummary).toBe(1);
    expect(started).toHaveLength(6);
  });

  it("does not issue a queued summary request after its tab has been abandoned", async () => {
    const abort = new AbortController();
    const releases: Array<() => void> = [];
    const fetcher = vi.fn((_url: RequestInfo | URL) => new Promise<Response>(resolve => {
      releases.push(() => resolve(json(null)));
    })) as unknown as typeof fetch;
    const done = fetchAiOperationsScope(
      filtersFromSearch(""), ["summary", "today", "month"],
      fetcher, new Date("2026-11-13T10:00:00Z"), abort.signal,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
    abort.abort();
    releases.shift()?.();
    await done;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

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
