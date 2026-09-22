import { describe, expect, it, vi } from "vitest";
import type { Asset, PlaybackTicket } from "./api";
import {
  createReviewPlaybackTicketCache,
  createReviewPrewarmQueue,
  reviewMediaOrigin,
  reviewPlaybackTicketReusable,
} from "./reviewVideoPerformance";

function asset(id: string): Asset {
  return {
    kind: "asset",
    asset_id: id,
    source_asset_id: "source-" + id,
    filename: id + ".mp4",
    media_type: "video/mp4",
    thumbnail_url: "/thumb/" + id,
    preview_url: "/preview/" + id,
  };
}

function ticket(overrides: Partial<PlaybackTicket> = {}): PlaybackTicket {
  return {
    url: "https://media.example.test/video-cache/example",
    cdn: true,
    expires_at: 200,
    ...overrides,
  };
}

describe("review video playback tickets", () => {
  it("reuses a CDN ticket while safely before expiry and deduplicates inflight requests", async () => {
    let now = 100;
    const fetchTicket = vi.fn(async () => ticket());
    const cache = createReviewPlaybackTicketCache(fetchTicket, { nowSeconds: () => now });
    const item = asset("one");

    const [first] = await Promise.all([cache.get(item), cache.get(item), cache.prefetch(item)]);
    expect(first.cdn).toBe(true);
    expect(fetchTicket).toHaveBeenCalledTimes(1);
    expect(cache.size()).toBe(1);
    expect(cache.peek(item)).toEqual(first);

    await cache.get(item);
    expect(fetchTicket).toHaveBeenCalledTimes(1);

    now = 186;
    expect(cache.peek(item)).toBeUndefined();
    await cache.get(item);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
  });

  it("briefly reuses provider fallback, then retries so a ready cache can promote to CDN", async () => {
    let now = 100;
    const fetchTicket = vi.fn()
      .mockResolvedValueOnce(ticket({ url: "/provider-preview", cdn: false, expires_at: null }))
      .mockResolvedValueOnce(ticket());
    const cache = createReviewPlaybackTicketCache(fetchTicket, {
      nowSeconds: () => now,
      fallbackTtlSeconds: 4,
    });
    const item = asset("fallback");

    expect((await cache.get(item)).cdn).toBe(false);
    expect(cache.peek(item)?.cdn).toBe(false);
    expect((await cache.get(item)).cdn).toBe(false);
    expect(fetchTicket).toHaveBeenCalledTimes(1);

    now = 104;
    expect((await cache.get(item)).cdn).toBe(true);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
  });

  it("extracts only CDN origins and applies an expiry safety window", () => {
    expect(reviewPlaybackTicketReusable(ticket(), 184)).toBe(true);
    expect(reviewPlaybackTicketReusable(ticket(), 185)).toBe(false);
    expect(reviewMediaOrigin(ticket())).toBe("https://media.example.test");
    expect(reviewMediaOrigin(ticket({ cdn: false, expires_at: null }))).toBeNull();
  });
});

describe("review video prewarm queue", () => {
  it("bounds concurrent prewarm work and deduplicates scheduled assets", async () => {
    const releases: Array<() => void> = [];
    let running = 0;
    let peak = 0;
    const run = vi.fn(() => new Promise<void>(resolve => {
      running += 1;
      peak = Math.max(peak, running);
      releases.push(() => {
        running -= 1;
        resolve();
      });
    }));
    const queue = createReviewPrewarmQueue(run, 2);
    const items = [asset("a"), asset("b"), asset("c")];

    expect(queue.enqueue(items[0])).toBe(true);
    expect(queue.enqueue(items[0])).toBe(false);
    expect(queue.enqueue(items[1])).toBe(true);
    expect(queue.enqueue(items[2])).toBe(true);
    expect(queue.snapshot()).toEqual({ active: 2, queued: 1, completed: 0 });
    expect(peak).toBe(2);

    releases.shift()!();
    await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(3));
    expect(peak).toBe(2);

    while (releases.length) releases.shift()!();
    await vi.waitFor(() => expect(queue.snapshot()).toEqual({ active: 0, queued: 0, completed: 3 }));
    expect(queue.enqueue(items[0])).toBe(false);
  });

  it("prioritizes interaction work and caps speculative viewport backlog", async () => {
    const releases: Array<() => void> = [];
    const callOrder: string[] = [];
    const run = vi.fn((item: Asset) => {
      callOrder.push(item.asset_id);
      return new Promise<void>(resolve => releases.push(resolve));
    });
    const queue = createReviewPrewarmQueue(run, 1, 2);
    const a = asset("a"), b = asset("b"), c = asset("c"), d = asset("d"), urgent = asset("urgent");

    expect(queue.enqueue(a)).toBe(true);
    expect(queue.enqueue(b)).toBe(true);
    expect(queue.enqueue(c)).toBe(true);
    expect(queue.enqueue(d)).toBe(false);
    expect(queue.enqueue(urgent, "high")).toBe(true);
    expect(queue.snapshot()).toEqual({ active: 1, queued: 2, completed: 0 });

    releases.shift()!();
    await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(2));
    expect(callOrder[1]).toBe("urgent");

    expect(queue.enqueue(c)).toBe(true);
    expect(queue.snapshot().queued).toBe(2);

    while (releases.length) {
      releases.shift()!();
      await Promise.resolve();
    }
  });
});
