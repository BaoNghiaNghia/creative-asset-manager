import { describe, expect, it } from "vitest";
import { reconcilePickedPage } from "./pickedPageReconciliation";
import type { Listing, Page } from "./api";

const row = (listingKey: string, status = "pending") =>
  ({ listingKey, picked: true, pageTitle: "Sample " + listingKey,
    designs: [], tiktokListing: { status } }) as Listing;

const page = (items: Listing[], total = items.length): Page<Listing> => ({
  items, page: 1, page_size: 50, total, pages: 1,
  last_synced_at: "2026-11-12T12:00:00Z", source_status: "online",
});

describe("Picked background poll reconciliation", () => {
  it("ignores identical polls and timestamp-only updates without resetting row identity", () => {
    const old = page([row("a"), row("b")]);
    const incoming = { ...page([row("a"), row("b")]),
      last_synced_at: "2026-11-12T12:00:30Z" };
    expect(reconcilePickedPage(old, incoming)).toBe(old);
  });

  it("updates only the changed job or Design while reusing other rows", () => {
    const unchanged = row("a");
    const old = page([unchanged, row("b")]);
    const updated = page([row("a"), row("b", "completed")]);
    const result = reconcilePickedPage(old, updated);
    expect(result).not.toBe(old);
    expect(result.items[0]).toBe(unchanged);
    expect(result.items[1].tiktokListing?.status).toBe("completed");
  });

  it("respects listing reorder, removal, totals and source status", () => {
    const a = row("a"), b = row("b");
    const old = page([a, b]);
    const reordered = reconcilePickedPage(old, page([row("b"), row("a")]));
    expect(reordered.items).toEqual([b, a]);
    expect(reordered.items[0]).toBe(b);
    expect(reconcilePickedPage(old, page([row("a")])).items).toHaveLength(1);
    expect(reconcilePickedPage(old, page([row("a"), row("b")], 42)).total).toBe(42);
    expect(reconcilePickedPage(old, { ...page([row("a"), row("b")]),
      source_status: "offline" }).source_status).toBe("offline");
  });
});
