import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset, AssetMetadata } from "../types";
import { AssetDetailsPanel, formatBytes, readableKind, resolvePreviewUrl } from "./AssetDetailsPanel";

const item: Asset = {
  provider: "google-drive",
  id: "drive-image-1",
  name: "Amazon_07_04_20260030_3.jpg",
  kind: "image",
  mime_type: "image/jpeg",
  size: 32_200_000,
  modified_at: "2026-04-14T10:00:00Z",
  thumbnail_url: "/api/explorer/media/drive-image-1?provider=google-drive&thumbnail=true",
  web_url: "https://drive.google.com/file/d/drive-image-1/view",
  ancestor_names: ["My Drive", "Amazon - Varsity & Pet"],
};

const metadata: AssetMetadata = {
  item_id: item.id,
  tag_ids: ["public"],
  rating: 4,
  processing_status: "indexed",
};

const noop = () => undefined;

describe("Asset details inspector", () => {
  it("renders a friendly Drive-style preview and file properties without an internal asset", () => {
    const markup = renderToStaticMarkup(<AssetDetailsPanel item={item} metadata={metadata} onClose={noop} onPreview={noop} />);
    for (const value of ["details", "activity", item.name, "Image · image/jpeg", "31 MB", "My Drive / Amazon - Varsity &amp; Pet", "Google Drive", "Open preview", "Open in Google Drive", "public", "indexed"]) {
      expect(markup).toContain(value);
    }
    expect(markup).not.toContain("Operator actions");
  });

  it("renders an accessible empty state when the panel is toggled without a selection", () => {
    const markup = renderToStaticMarkup(<AssetDetailsPanel item={null} onClose={noop} />);
    expect(markup).toContain("Select a file or folder");
    expect(markup).toContain('aria-label="File information"');
    expect(markup).toContain('aria-label="Close file information"');
  });

  it("uses a source preview when a detail panel was opened without an Explorer item", () => {
    expect(resolvePreviewUrl(null, { preview_url: "/api/explorer/media/external-1?provider=google-drive" })).toBe("/api/explorer/media/external-1?provider=google-drive");
    expect(resolvePreviewUrl(item, { preview_url: "/api/explorer/media/other" })).toBe(item.thumbnail_url);
  });

  it("formats provider sizes and kinds deterministically", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(undefined)).toBe("Not available");
    expect(readableKind("video")).toBe("Video");
  });
});
