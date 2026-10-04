import { describe, expect, it } from "vitest";
import source from "./App.tsx?raw";

describe("video search UI wiring", () => {
  it("always searches images and videos without a media toggle", () => {
    expect(source).not.toContain('className="search-mode-tabs"');
    expect(source).not.toContain('aria-label="Search media type"');
    expect(source).toContain('const explorer = useDriveExplorer(true)');
    expect(source).toContain('enabled: true');
    expect(source).not.toContain("<SearchGuide");
    expect(source).not.toContain("Advanced filters apply to image results.");
  });

  it("renders only result groups that are relevant to the current search", () => {
    expect(source).toContain("showImageSearchSection");
    expect(source).toContain("showVideoSearchSection");
    expect(source).toContain("showMixedSearchEmpty");
    expect(source).toContain('{showVideoSearchSection && <section className="mixed-search-section"');
    expect(source).toContain('hidden={hasSearchQuery && (!showImageSearchSection || !imageResultsExpanded)}');
    expect(source).toContain('<VideoSearchResults items={videoSearch.items} onOpen={setPlaybackItem} onDetails={openVideoDetails} />');
    expect(source).toContain('>{videoSearch.loadingMore ? "Đang tải…" : "Hiển thị thêm"}</button>');
    expect(source).toContain('>{explorer.searchV3.loadingMore ? "Đang tải…" : "Hiển thị thêm"}</button>');
    expect(source).not.toContain('resetKey={`${paginationResetKey}:${explorer.searchV3.items.length}`}');
    expect(source).not.toContain('searchMediaMode === "videos"');
  });

  it("uses one shared empty state when image and video searches both settle with zero results", () => {
    expect(source).toContain("const showMixedSearchEmpty = hasSearchQuery");
    expect(source).toContain("&& !showImageSearchSection");
    expect(source).toContain("&& !showVideoSearchSection");
    expect(source).toContain("{showMixedSearchEmpty && <EmptyAssets");
    expect(source).toContain("{hasSearchQuery && showImageSearchSection && <SearchCategoryFilter");
  });
  it("keeps the selected video analysis payload available to the details panel", () => {
    expect(source).toContain("detailsVideoAnalysis");
    expect(source).toContain("setDetailsVideoAnalysis(item)");
    expect(source).toContain("videoAnalysis={detailsVideoAnalysis}");
  });

  it("allows image and video result groups to collapse independently", () => {
    expect(source).toContain("imageResultsExpanded");
    expect(source).toContain("videoResultsExpanded");
    expect(source).toContain('aria-controls="mixed-image-results"');
    expect(source).toContain('aria-controls="mixed-video-results"');
    expect(source).toContain("setImageResultsExpanded(value => !value)");
    expect(source).toContain("setVideoResultsExpanded(value => !value)");
    expect(source).toContain('<i aria-hidden="true">{imageResultsExpanded ? "−" : "+"}</i><span>Images');
    expect(source).toContain('<i aria-hidden="true">{videoResultsExpanded ? "−" : "+"}</i><span>Videos');
  });


});
