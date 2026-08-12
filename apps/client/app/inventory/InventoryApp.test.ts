import { describe, expect, it, vi } from "vitest";
import { routeForPath } from "../AppRoute";
import { inventoryApi } from "./api";

describe("Inventory routes and API boundary", () => {
  it("resolves all six Inventory routes without changing Creative routes", () => {
    for (const path of ["/inventory", "/inventory/inbox", "/inventory/review", "/inventory/daily", "/inventory/reports", "/inventory/settings"]) expect(routeForPath(path)).toBe("inventory");
    expect(routeForPath("/")).toBe("explorer");
  });
  it("uses only the Inventory API prefix for daily operations", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "run", blockers: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await inventoryApi.getDailyRun("2030-08-09");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/inventory/daily-runs/2030-08-09");
    vi.unstubAllGlobals();
  });
  it("uses only the Inventory API prefix for review operations", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await inventoryApi.listReviews();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/inventory/reviews");
    vi.unstubAllGlobals();
  });
});