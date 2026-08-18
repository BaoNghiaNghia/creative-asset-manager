import { describe, expect, it } from "vitest";
import source from "./App.tsx?raw";

describe("video search UI wiring", () => {
  it("keeps Images as the default and renders accessible separate tabs", () => {
    expect(source).toContain('DEFAULT_SEARCH_MODE = "images"');
    expect(source).toContain('role="tablist"');
    expect(source).toContain('id="search-mode-images"');
    expect(source).toContain('id="search-mode-videos"');
  });

  it("keeps video loading, empty and safe error states isolated from image results", () => {
    expect(source).toContain("videoSearch.loading ? <AssetGridSkeleton />");
    expect(source).toContain("No videos matched this search.");
    expect(source).toContain("videoSearch.error && <div className=\"search-warning\"");
    expect(source).toContain('<VideoSearchResults items={videoSearch.items} />');
    expect(source).toContain('searchMode === "images" && explorer.selected.size');
  });
});
