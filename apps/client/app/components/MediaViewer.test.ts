import { describe, expect, it } from "vitest";
import { mediaPreviewUrl } from "./MediaViewer";

describe("mediaPreviewUrl", () => {
  it("keeps the selected external source in a safe preview URL", () => {
    expect(mediaPreviewUrl({
      id: "drive-file-id",
      provider: "google-drive",
      external_source_id: "source-123",
    })).toBe("/api/explorer/media/drive-file-id?provider=google-drive&external_source_id=source-123");
  });

  it("preserves backwards compatibility when a source is unavailable", () => {
    expect(mediaPreviewUrl({ id: "file", provider: "google-drive" }))
      .toBe("/api/explorer/media/file?provider=google-drive");
  });
});
