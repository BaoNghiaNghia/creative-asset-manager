import { describe, expect, it } from "vitest";
import { mediaPreviewUrl, previewUnavailableMessage } from "./MediaViewer";
import { isAvifAsset } from "../utils/fileType";

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


describe("previewUnavailableMessage", () => {
  it("explains an AVIF preview failure without exposing provider details", () => {
    expect(previewUnavailableMessage("image/avif"))
      .toBe("This AVIF image could not be previewed by this browser or the connected cloud provider.");
  });

  it("keeps the generic message for other media types", () => {
    expect(previewUnavailableMessage("image/png"))
      .toBe("The connected cloud provider could not stream this file.");
  });
});


describe("AVIF preview routing", () => {
  it("uses the media endpoint with provider scope for AVIF assets", () => {
    expect(isAvifAsset({ name: "photo.avif", mime_type: "application/octet-stream" })).toBe(true);
    expect(mediaPreviewUrl({ id: "avif-1", provider: "google-drive", external_source_id: "source-1" }))
      .toBe("/api/explorer/media/avif-1?provider=google-drive&external_source_id=source-1");
  });

  it("keeps non-AVIF media behavior unchanged", () => {
    expect(isAvifAsset({ name: "photo.png", mime_type: "image/png" })).toBe(false);
  });
});
