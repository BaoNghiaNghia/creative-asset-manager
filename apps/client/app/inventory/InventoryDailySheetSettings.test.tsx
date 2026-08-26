import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  inventoryDailySheetApi: {
    getConfiguration: vi.fn(() => new Promise(() => undefined)),
    getStatus: vi.fn(() => new Promise(() => undefined)),
  },
}));

import { InventoryDailySheetSettings, dailySheetActionVisibility, inventorySettingsMode } from "./InventoryDailySheetSettings";

describe("InventoryDailySheetSettings", () => {
  it("shows separate feature flags and guarded operations", () => {
    const markup = renderToStaticMarkup(<InventoryDailySheetSettings />);
    expect(markup).toContain("Pipeline hình ảnh");
    expect(markup).toContain("Tự động xử lý Google Sheets");
    expect(markup).toContain("Xem trước báo cáo");
    expect(markup).toContain("Kho/SKU cũ");
    expect(markup).toContain("Kiểm kho hằng ngày");
    expect(markup).toContain("Quét workbook");
    expect(markup).toContain("CHƯA SẴN SÀNG");
    expect(markup).toContain('<div class="inventory-settings-hero">');
    expect(markup).not.toContain('<header class="inventory-settings-hero">');
    expect(markup).toContain("Thao tác thủ công");
    expect(markup).toContain("Cấu hình JSON nâng cao");
  });

  it("recognizes V4.1 without presenting it as a legacy configuration", () => {
    expect(inventorySettingsMode({ version: 4, mode: "gemini_tool_sheet_agent" })).toBe("v4");
    expect(inventorySettingsMode({ version: 2, mode: "daily_count_sheet" })).toBe("daily_count_sheet");
    expect(inventorySettingsMode({})).toBe("legacy");
  });

  it("separates safe report execution from target-table writes", () => {
    expect(dailySheetActionVisibility(true, "report_only")).toEqual({
      runReport: true,
      applyWrites: false,
    });
    expect(dailySheetActionVisibility(true, "target_table")).toEqual({
      runReport: false,
      applyWrites: true,
    });
    expect(dailySheetActionVisibility(false, "legacy")).toEqual({
      runReport: false,
      applyWrites: true,
    });
  });
});
