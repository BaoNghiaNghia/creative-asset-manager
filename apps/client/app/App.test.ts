import { describe, expect, it } from "vitest";
import { formatSearchDuration, getAnalysisSelectionState, getSearchSuggestionKeyAction, isEligibleAnalysisItem, curateSearchSuggestions } from "./App";
import { pruneSelectedIds } from "./hooks/useDriveExplorer";
import { isSearchRequestInFlight, shouldFetchSearchSuggestions } from "./hooks/useSearchV2";
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


describe("Search suggestions eligibility", () => {
  it("loads suggestions only for authenticated modern search with two or more characters", () => {
    expect(shouldFetchSearchSuggestions(true, true, "mi")).toBe(true);
    expect(shouldFetchSearchSuggestions(true, true, "m")).toBe(false);
    expect(shouldFetchSearchSuggestions(false, true, "milo")).toBe(false);
    expect(shouldFetchSearchSuggestions(true, false, "milo")).toBe(false);
  });
});


describe("Search suggestion keyboard actions", () => {
  it("submits the typed query and dismisses suggestions when Enter has no active option", () => {
    expect(getSearchSuggestionKeyAction("Enter", 4, -1)).toBe("submit");
    expect(getSearchSuggestionKeyAction("Enter", 0, -1)).toBe("submit");
  });

  it("selects an active suggestion and preserves arrow navigation", () => {
    expect(getSearchSuggestionKeyAction("Enter", 4, 2)).toBe("select");
    expect(getSearchSuggestionKeyAction("ArrowDown", 4, -1)).toBe("next");
    expect(getSearchSuggestionKeyAction("ArrowUp", 4, 0)).toBe("previous");
  });
});


describe("Search loading state", () => {
  it("does not report an in-flight search after the query is cleared", () => {
    expect(isSearchRequestInFlight("", true)).toBe(false);
    expect(isSearchRequestInFlight("   ", true)).toBe(false);
    expect(isSearchRequestInFlight("nurse", true)).toBe(true);
  });
});

describe("Search suggestion curation", () => {
  it("keeps short continuations, sorts them and removes long metadata sentences", () => {
    const make = (text: string) => ({ text, prefix: text, completion: "", kind: "search_text" as const });
    expect(curateSearchSuggestions("horse", [
      make("horse personalized product photo with a stethoscope sweatshirt"),
      make("horses needlework personalized gift"),
      make("horse"),
      make("horses needlework"),
      make("unrelated"),
    ]).map(item => item.text)).toEqual(["horse", "horses needlework", "horses needlework personalized gift"]);
  });
});
