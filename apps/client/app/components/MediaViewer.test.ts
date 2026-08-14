import { describe, expect, it } from "vitest";
import { mediaPreviewUrl, previewUnavailableMessage, readTextPreview, TEXT_PREVIEW_MAX_BYTES, TEXT_PREVIEW_RANGE } from "./MediaViewer";
import { isAvifAsset } from "../utils/fileType";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";

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


describe("shared asset URL helpers", () => {
  it("uses one scoped preview URL for AVIF and preserves source parameters", () => {
    const item = { id: "avif", name: "PHOTO.AVIF", mime_type: "application/octet-stream", provider: "google-drive" as const, external_source_id: "source-a" };
    expect(assetPreviewUrl(item)).toBe("/api/explorer/preview/avif?provider=google-drive&external_source_id=source-a");
    expect(explorerAssetUrl(item, "preview")).toBe(assetPreviewUrl(item));
  });

  it("keeps non-AVIF media URLs on the existing media endpoint", () => {
    const item = { id: "png", name: "photo.png", mime_type: "image/png", provider: "google-drive" as const, external_source_id: "source-a" };
    expect(assetPreviewUrl(item)).toBe("/api/explorer/media/png?provider=google-drive&external_source_id=source-a");
  });
});

describe("TXT preview reader", () => {
  it("caps ignored Range responses at 1 MiB and decodes literal text safely", async () => {
    const controller = new AbortController();
    const source = new Uint8Array(TEXT_PREVIEW_MAX_BYTES + 20).fill(65);
    const response = new Response(source, { status: 200 });
    const result = await readTextPreview(response, controller.signal);
    expect(TEXT_PREVIEW_RANGE).toBe("bytes=0-1048575");
    expect(result.text).toHaveLength(TEXT_PREVIEW_MAX_BYTES);
    expect(result.truncated).toBe(true);
  });

  it("marks a partial response as truncated", async () => {
    const controller = new AbortController();
    const response = new Response("<script>alert(1)</script>", {
      status: 206, headers: { "content-range": "bytes 0-24/3000000" },
    });
    const result = await readTextPreview(response, controller.signal);
    expect(result.text).toBe("<script>alert(1)</script>");
    expect(result.truncated).toBe(true);
  });
});
