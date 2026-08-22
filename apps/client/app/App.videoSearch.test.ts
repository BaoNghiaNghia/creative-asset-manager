import { describe, expect, it } from "vitest";
import source from "./App.tsx?raw";

describe("video search UI wiring", () => {
  it("defaults to All with an accessible compact media selector", () => {
    expect(source).toContain('DEFAULT_SEARCH_MEDIA_MODE = "all"');
    expect(source).toContain('role="radiogroup"');
    expect(source).toContain('aria-label="Search media type"');
    expect(source).toContain('["all", "images", "videos"] as SearchMediaMode[]');
    expect(source).not.toContain("<SearchGuide");
    expect(source).not.toContain("Advanced filters apply to image results.");
  });

  it("renders independent video results in All mode and keeps video-only isolated", () => {
    expect(source).toContain('searchMediaMode === "all" && explorer.query.trim()');
    expect(source).toContain('mixed-search-section');
    expect(source).toContain('<VideoSearchResults items={videoSearch.items} onOpen={setPlaybackItem} />');
    expect(source).toContain('searchMediaMode === "videos" && explorer.query.trim() ? videoResults');
    expect(source).toContain('(!explorer.query.trim() || imageSearchEnabled) && explorer.selected.size');
  });
  it("allows image and video result groups to collapse independently", () => {
    expect(source).toContain("imageResultsExpanded");
    expect(source).toContain("videoResultsExpanded");
    expect(source).toContain('aria-controls="mixed-image-results"');
    expect(source).toContain('aria-controls="mixed-video-results"');
    expect(source).toContain("setImageResultsExpanded(value => !value)");
    expect(source).toContain("setVideoResultsExpanded(value => !value)");
  });


});
