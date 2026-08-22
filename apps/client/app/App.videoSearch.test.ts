import { describe, expect, it } from "vitest";
import source from "./App.tsx?raw";

describe("video search UI wiring", () => {
  it("defaults to All with an accessible compact media selector", () => {
    expect(source).toContain('DEFAULT_SEARCH_MEDIA_MODE = "all"');
    expect(source).toContain('role="radiogroup"');
    expect(source).toContain('aria-label="Search media type"');
    expect(source).toContain('["all", "images", "videos"] as SearchMediaMode[]');
  });

  it("renders independent video results in All mode and keeps video-only isolated", () => {
    expect(source).toContain('searchMediaMode === "all" && explorer.query.trim()');
    expect(source).toContain('mixed-search-section');
    expect(source).toContain('<VideoSearchResults items={videoSearch.items} onOpen={setPlaybackItem} />');
    expect(source).toContain('searchMediaMode === "videos" && explorer.query.trim() ? videoResults');
    expect(source).toContain('(!explorer.query.trim() || imageSearchEnabled) && explorer.selected.size');
  });
});
