import { describe, expect, it } from "vitest";
import { formatSearchDuration, getAnalysisSelectionState, isEligibleAnalysisItem } from "./App";
import { pruneSelectedIds } from "./hooks/useDriveExplorer";
import type { Asset } from "./types";

function asset(overrides: Partial<Asset>): Asset {
  return { provider: "google-drive", id: "item-1", name: "asset", kind: "image", mime_type: "image/jpeg", ...overrides };
}

describe("Analyze metadata selection", () => {
  it("enables one imported image", () => {
    const item = asset({ internal_asset_id: "asset-1" });
    const selection = getAnalysisSelectionState(new Set([item.id]), [item]);
    expect(selection.complete).toBe(true);
    expect(selection.assetIds).toEqual(["asset-1"]);
  });

  it("disables an image that is not imported", () => {
    const item = asset({ internal_asset_id: "" });
    const selection = getAnalysisSelectionState(new Set([item.id]), [item]);
    expect(selection.complete).toBe(false);
    expect(selection.tooltip).toBe("Selected images are still being imported. Refresh the folder and try again.");
  });

  it("disables folders and non-images", () => {
    for (const item of [asset({ kind: "folder", internal_asset_id: "folder" }), asset({ kind: "video", internal_asset_id: "video" })]) {
      expect(isEligibleAnalysisItem(item)).toBe(false);
      expect(getAnalysisSelectionState(new Set([item.id]), [item]).tooltip).toBe("Only imported image files can be analyzed.");
    }
  });

  it("prunes hidden stale selections", () => {
    const item = asset({ id: "visible", internal_asset_id: "asset-1" });
    expect([...pruneSelectedIds(new Set(["visible", "hidden"]), [item])]).toEqual(["visible"]);
    expect(getAnalysisSelectionState(new Set(["visible", "hidden"]), [item]).tooltip).toBe("Selection changed. Reselect the visible images.");
  });

  it("enables the same selection after a refresh returns an internal asset id", () => {
    const beforeRefresh = asset({ internal_asset_id: undefined });
    const afterRefresh = asset({ internal_asset_id: "asset-1" });
    expect(getAnalysisSelectionState(new Set([beforeRefresh.id]), [beforeRefresh]).complete).toBe(false);
    expect(getAnalysisSelectionState(new Set([afterRefresh.id]), [afterRefresh]).complete).toBe(true);
  });
});


describe("formatSearchDuration", () => {
  it("formats the completed search duration for the compact search indicator", () => {
    expect(formatSearchDuration(42)).toBe("42 ms");
    expect(formatSearchDuration(1_250)).toBe("1.25 s");
    expect(formatSearchDuration(null)).toBeNull();
  });
});
