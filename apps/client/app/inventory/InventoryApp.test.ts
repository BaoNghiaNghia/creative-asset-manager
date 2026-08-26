import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { routeForPath } from "../AppRoute";
import { inventoryApi, inventoryMaterialApi } from "./api";
import { InventoryApp } from "./InventoryApp";

describe("Inventory routes and API boundary", () => {
  it("renders Inventory navigation as embedded modal content without the standalone header", () => {
    const markup = renderToStaticMarkup(createElement(InventoryApp as any, { embedded: true, initialPage: "dashboard" }));
    expect(markup).toContain("inventory-shell--embedded");
    expect(markup).toContain("<button");
    expect(markup).toContain("Inventory dashboard");
    expect(markup).not.toContain("inventory-brand");
  });
  it("resolves all six Inventory routes without changing Creative routes", () => {
    for (const path of ["/inventory", "/inventory/inbox", "/inventory/review", "/inventory/materials", "/inventory/daily", "/inventory/reports", "/inventory/settings"]) expect(routeForPath(path)).toBe("inventory");
    expect(routeForPath("/")).toBe("explorer");
  });
  it("uses the tenant Inventory API boundary for the material registry", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await inventoryMaterialApi.list();
    await inventoryMaterialApi.candidates();
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      "/api/inventory/materials",
      "/api/inventory/materials/candidates",
    ]);
    vi.unstubAllGlobals();
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