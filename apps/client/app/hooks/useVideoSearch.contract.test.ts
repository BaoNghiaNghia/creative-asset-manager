import { describe, expect, it } from "vitest";
import source from "./useVideoSearch.ts?raw";
import explorerSource from "./useDriveExplorer.ts?raw";

describe("video search request isolation", () => {
  it("uses only the video endpoint, source scope, and never sends tenant identity", () => {
    expect(source).toContain('fetch("/api/v1/search/video"');
    expect(source).toContain("external_source_id: externalSourceId");
    expect(source).not.toContain("tenant_id");
  });

  it("uses explicit image-search enablement instead of UI mode strings", () => {
    expect(explorerSource).toContain("useDriveExplorer(imageSearchEnabled = true)");
    expect(explorerSource).toContain("imageSearchEnabled ? query : \"\"");
    expect(explorerSource).not.toContain('searchMode === \"images\"');
  });
});
