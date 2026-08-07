import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset, AssetMetadata } from "../types";
import { AssetDetailsPanel, buildActivity, formatBytes, inferKind, readableKind, resolvePreviewUrl, resolveProviderWebUrl, resolveLocation } from "./AssetDetailsPanel";

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

  it("shows the longest available breadcrumb and ignores placeholder location values", () => {
    expect(resolveLocation({ ...item, folder_path: "Current folder", ancestor_names: ["Desify - Image & Video Assets", "Etsy - VienLuna", "listing - 4467905366"] }, {})).toBe("Desify - Image & Video Assets / Etsy - VienLuna / listing - 4467905366");
    expect(resolveLocation({ ...item, folder_path: "Current folder", ancestor_names: [] }, { source_metadata: { path: "Desify - Image & Video Assets / Etsy - VienLuna" } })).toBe("Desify - Image & Video Assets / Etsy - VienLuna");
  });

  it("previews AVIF files when the provider reports octet-stream", () => {
    const avif = { ...item, name: "photo.avif", mime_type: "application/octet-stream", thumbnail_url: "/api/explorer/media/avif-1?provider=google-drive" };
    const markup = renderToStaticMarkup(<AssetDetailsPanel item={avif} metadata={metadata} onClose={noop} onPreview={noop} />);
    expect(markup).toContain('<img src="/api/explorer/preview/drive-image-1?provider=google-drive"');
    expect(markup).toContain("image/avif");
  });

  it("turns technical processing states into an understandable activity timeline", () => {
    const entries = buildActivity(item, {
      asset: {}, sources: [], storage: [], active_analysis: null,
      analysis_history: [{ id: "analysis-1", status: "completed", ai_provider: "gemini", ai_model: "gemini-3.5-flash-lite", completed_at: "2026-07-27T10:00:00Z" }],
      analysis_total: 1,
      jobs: [
        { id: "index-1", job_type: "asset_index", status: "failed", last_error_code: "search_provider_unconfigured", updated_at: "2026-07-27T10:01:00Z" },
        { id: "projection-1", job_type: "search_projection_build", status: "completed", completed_at: "2026-07-27T09:59:00Z" },
      ],
      job_total: 2, pipelines: [], lifecycle_status: "search_failed", can_administer: true,
      limits: { max_json_nodes: 10, max_json_depth: 3 },
    });
    expect(entries.map(entry => entry.title)).toContain("Search index updated failed");
    expect(entries.map(entry => entry.title)).toContain("Search data prepared");
    expect(entries.map(entry => entry.title)).toContain("AI metadata analysis completed");
    expect(entries.find(entry => entry.id === "job-index-1")?.detail).toContain("Search Provider Unconfigured");
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

  it("resolves a trusted provider link and rejects untrusted links", () => {
    expect(resolveProviderWebUrl(item, {}, "google-drive")).toBe("https://drive.google.com/file/d/drive-image-1/view");
    expect(resolveProviderWebUrl({ ...item, web_url: "https://evil.example/file" }, { source_metadata: { webViewLink: "https://docs.google.com/document/d/drive-image-1/edit" } }, "google-drive")).toBe("https://docs.google.com/document/d/drive-image-1/edit");
    expect(resolveProviderWebUrl({ ...item, web_url: undefined }, { source_metadata: { web_url: "https://evil.example/file" } }, "sharepoint")).toBeUndefined();
  });
  it("formats provider sizes and kinds deterministically", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(undefined)).toBe("Not available");
    expect(readableKind("video")).toBe("Video");
    expect(inferKind("application/octet-stream", "photo.avif")).toBe("image");
    expect(inferKind(undefined, "photo.webp")).toBe("image");
  });
});