import { describe, expect, it } from "vitest";
import source from "./useVideoSearch.ts?raw";
import explorerSource from "./useDriveExplorer.ts?raw";

describe("video search request isolation", () => {
  it("uses only the VIDEO-7A endpoint and never sends tenant identity", () => {
    expect(source).toContain('fetch("/api/v1/search/video"');
    expect(source).toContain('JSON.stringify({ query: normalizedQuery, limit: VIDEO_SEARCH_LIMIT })');
    expect(source).not.toContain("tenant_id");
  });

  it("suppresses image search requests while Videos is selected", () => {
    expect(explorerSource).toContain('searchMode === "images" ? query : ""');
  });
});
