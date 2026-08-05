import { describe, expect, it } from "vitest";
import driveExplorerSource from "./useDriveExplorer.ts?raw";
import searchHookSource from "./useSearchV3.ts?raw";

describe("Search V3 runtime contract", () => {
  it("never invokes legacy Explorer search or indexing while typing", () => {
    for (const endpoint of [
      "/api/explorer/search",
      "/api/explorer/search/stream",
      "/api/explorer/index/start",
      "/api/explorer/index/status",
    ]) {
      expect(driveExplorerSource).not.toContain(endpoint);
    }
  });

  it("never mutates capabilities to V1 or V2 after request failures", () => {
    expect(searchHookSource).not.toContain('selected_version: "v1"');
    expect(searchHookSource).not.toContain('selected_version: "v2"');
    expect(searchHookSource).not.toContain("/api/explorer/");
  });

  it("gates viewer browse and search requests behind bootstrap source selection", () => {
    expect(driveExplorerSource).toContain("useSearchV3(auth.authenticated && explorerReady");
    expect(driveExplorerSource).toContain("if (pureViewer && !externalSourceId)");
    expect(driveExplorerSource).toContain("if (pureViewer && !sourceId)");
    expect(driveExplorerSource).toContain("/api/explorer/viewer/bootstrap?provider=");
    expect(driveExplorerSource).toContain("abortPendingBrowse();");
    expect(driveExplorerSource).toContain("folderCache.current.clear();");
  });
});
