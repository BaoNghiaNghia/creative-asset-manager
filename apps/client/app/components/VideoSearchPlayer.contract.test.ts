import { describe, expect, it } from "vitest";
import source from "./VideoSearchPlayer.tsx?raw";

describe("video player lifecycle contract", () => {
  it("seeks only after metadata and gives each selected analysis its own media element", () => {
    expect(source).toContain("onLoadedMetadata={seekToBestMatch}");
    expect(source).toContain("key={item.analysis_run_id}");
    expect(source).toContain("[item.analysis_run_id, item.best_match.start_ms]");
  });

  it("releases native media on close or selection change without fetching blobs", () => {
    expect(source).toContain("video.pause()");
    expect(source).toContain('video.removeAttribute("src")');
    expect(source).toContain("video.load()");
    expect(source).not.toContain("arrayBuffer");
    expect(source).not.toContain("Blob");
    expect(source).not.toContain("fetch(");
  });
});
