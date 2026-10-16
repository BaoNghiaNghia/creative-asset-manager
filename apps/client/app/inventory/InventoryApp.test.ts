import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { routeForPath } from "../AppRoute";
import { inventoryApi, inventoryMaterialApi } from "./api";
import { InventoryApp, formatInventoryMaterialDate, inventoryMaterialDraftReady, inventoryReadinessBlockerLabel, loadInventoryMaterialWorkspace } from "./InventoryApp";

describe("Inventory routes and API boundary", () => {
  it("renders Inventory navigation as embedded modal content without the standalone header", () => {
    const markup = renderToStaticMarkup(createElement(InventoryApp as any, { embedded: true, initialPage: "dashboard" }));
    expect(markup).toContain("inventory-shell--embedded");
    expect(markup).toContain("<button");
    expect(markup).toContain("Inventory dashboard");
    expect(markup).toContain("Nguyên vật liệu");
    expect(markup).toContain("Gemini Prompts");
    expect(markup).toContain("C\u1ea5u h\u00ecnh");
    expect(markup).not.toContain(">Inbox<");
    expect(markup).not.toContain(">Reports<");
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
    await inventoryMaterialApi.readiness();
    await inventoryMaterialApi.createWarehouse({ code: "HN", name: "Kho Hà Nội" });
    await inventoryMaterialApi.approveEquivalent("candidate 31", { item_id: "item-1", expected_count: 2, expected_candidate_ids: ["candidate 31", "candidate 32"] });
    await inventoryMaterialApi.refreshEvidence();
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual([
      "/api/inventory/materials",
      "/api/inventory/materials/candidates",
      "/api/inventory/materials/readiness",
      "/api/inventory/materials/warehouses",
      "/api/inventory/materials/candidates/candidate%2031/approve-equivalent",
      "/api/inventory/materials/candidates/refresh-evidence",
    ]);
    expect(fetchMock.mock.calls[3][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[4][1]?.method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[4][1]?.body as string)).toEqual({item_id:"item-1",expected_count:2,expected_candidate_ids:["candidate 31","candidate 32"]});
    expect(fetchMock.mock.calls[5][1]?.method).toBe("POST");
    vi.unstubAllGlobals();
  });
  it("presents the Materials workspace in Vietnamese with compact guidance", () => {
    const markup = renderToStaticMarkup(createElement(InventoryApp as any, { embedded: true, initialPage: "materials" }));
    expect(markup).toContain("Nguyên vật liệu");
    expect(markup).toContain("Danh sách vật tư chuẩn");
    expect(markup).toContain("Hàng đợi xem xét");
    expect(markup).toContain("Tìm theo tên, nhóm hoặc mã");
    expect(markup).not.toContain("Canonical materials");
  });
  it("retains materials when only readiness fails and never claims carry-forward is safe", async () => {
    const fetchMock = vi.fn(async (url: string) => url.endsWith("/readiness")
      ? { ok: false, status: 503, json: async () => ({detail:{code:"temporary_inventory_readiness_failure"}}) }
      : { ok: true, json: async () => ({items: url.endsWith("/candidates") ? [{id:"pending"}] : [{material_id:"cotton"}]}) });
    vi.stubGlobal("fetch", fetchMock);
    try {
      const state = await loadInventoryMaterialWorkspace();
      expect(state.items).toEqual([{material_id:"cotton"}]);
      expect(state.candidates).toEqual([{id:"pending"}]);
      expect(state.readiness).toBeNull();
      expect(state.readinessFailed).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it("does not hide failures for required materials or candidate data", async () => {
    const fetchMock = vi.fn(async (url: string) => url.endsWith("/materials")
      ? {ok:false,status:403,json:async()=>({detail:{code:"forbidden"}})}
      : {ok:true,json:async()=>({items:[]})});
    vi.stubGlobal("fetch", fetchMock);
    try {
      await expect(loadInventoryMaterialWorkspace()).rejects.toThrow();
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it("labels readiness blockers without disguising unknown codes as missing materials", () => {
    expect(inventoryReadinessBlockerLabel("missing_material_registry")).toBe("Chưa có danh mục vật tư chuẩn");
    expect(inventoryReadinessBlockerLabel("missing_warehouse_registry")).toBe("Chưa xác nhận danh mục kho");
    expect(inventoryReadinessBlockerLabel("unresolved_material_candidates")).toBe("Có vật tư đang chờ duyệt");
    expect(inventoryReadinessBlockerLabel("new_provider_check")).toBe("Có điều kiện đối soát chưa được xác minh");
  });
  it("formats material timestamps for Vietnamese users", () => {
    expect(formatInventoryMaterialDate(null)).toBe("Chưa có dữ liệu");
    expect(formatInventoryMaterialDate("invalid")).toBe("invalid");
  });
  it("requires both material dimension and unit before creating a new catalog item", () => {
    expect(inventoryMaterialDraftReady(undefined)).toBe(false);
    expect(inventoryMaterialDraftReady({ canonicalDimension: "mass", preferredUnit: "" })).toBe(false);
    expect(inventoryMaterialDraftReady({ canonicalDimension: "", preferredUnit: "g" })).toBe(false);
    expect(inventoryMaterialDraftReady({ canonicalDimension: "mass", preferredUnit: "g" })).toBe(true);
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
