import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  inventoryDailySheetApi: {
    getConfiguration: vi.fn(() => new Promise(() => undefined)),
    getStatus: vi.fn(() => new Promise(() => undefined)),
  },
}));

import { InventoryDailySheetSettings, dailySheetActionVisibility } from "./InventoryDailySheetSettings";

describe("InventoryDailySheetSettings", () => {
  it("shows separate feature flags and guarded operations", () => {
    const markup = renderToStaticMarkup(<InventoryDailySheetSettings />);
    expect(markup).toContain("Image pipeline enabled");
    expect(markup).toContain("Daily Sheets automation enabled");
    expect(markup).toContain("Preview report");
    expect(markup).toContain("Legacy warehouse/SKU");
    expect(markup).toContain("Daily count sheet");
    expect(markup).toContain("Scan Workbook");
    expect(markup).toContain("AUTOMATION BLOCKED");
    expect(markup).toContain("Run snapshot and reset");
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
