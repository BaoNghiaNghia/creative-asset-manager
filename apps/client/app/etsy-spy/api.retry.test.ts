import { afterEach, describe, expect, it, vi } from "vitest";
import { spyFetch } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Etsy Spy transient gateway retry", () => {
  it("retries exactly once for a GET after a 502 and accepts the next 200", async () => {
    vi.useFakeTimers();
    const network = vi.fn()
      .mockResolvedValueOnce(new Response("Bad gateway", { status: 502 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ total: 4 }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }));
    vi.stubGlobal("fetch", network);
    const request = spyFetch<{ total: number }>("/listings?page=1");
    await vi.advanceTimersByTimeAsync(2000);
    expect(await request).toEqual({ total: 4 });
    expect(network).toHaveBeenCalledTimes(2);
    expect(network.mock.calls[0][1].credentials).toBe("include");
  });

  it("honors bounded Retry-After and never retries writes", async () => {
    const network = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Retry later" }), {
        status: 503, headers: { "Retry-After": "4" },
      }),
    );
    vi.stubGlobal("fetch", network);
    await expect(spyFetch("/listings/pick", {
      method: "POST", body: JSON.stringify({ listingKey: "id" }),
    })).rejects.toThrow("Retry later");
    expect(network).toHaveBeenCalledTimes(1);
  });

  it("does not retry authentication failures", async () => {
    const network = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "No session" }), { status: 401 }),
    );
    vi.stubGlobal("fetch", network);
    await expect(spyFetch("/summary")).rejects.toThrow("No session");
    expect(network).toHaveBeenCalledTimes(1);
  });

  it("cancels a queued retry when the view unmounts", async () => {
    const network = vi.fn().mockResolvedValue(
      new Response("Bad gateway", { status: 502 }),
    );
    vi.stubGlobal("fetch", network);
    vi.useFakeTimers();
    const controller = new AbortController();
    const pending = spyFetch("/summary", { signal: controller.signal });
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(network).toHaveBeenCalledTimes(1);
  });
});
