import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  inventoryDailySheetApi: {
    getConfiguration: vi.fn(() => new Promise(() => undefined)),
    getStatus: vi.fn(() => new Promise(() => undefined)),
  },
}));

import { InventoryDailySheetSettings } from "./InventoryDailySheetSettings";

describe("InventoryDailySheetSettings", () => {
  it("shows separate feature flags and guarded operations", () => {
    const markup = renderToStaticMarkup(<InventoryDailySheetSettings />);
    expect(markup).toContain("Image pipeline enabled");
    expect(markup).toContain("Daily Sheets automation enabled");
    expect(markup).toContain("Preview reconciliation");
    expect(markup).toContain("Run snapshot and reset");
  });
});
