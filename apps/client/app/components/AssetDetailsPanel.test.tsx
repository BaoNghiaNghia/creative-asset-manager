import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset, AssetMetadata } from "../types";
import { AssetActionIcon, AssetDetailsPanel, VideoAnalysisDetails, VideoGenerationPrompts, buildVideoGenerationPrompts, buildActivity, formatBytes, formatVideoTimestamp, inferKind, readableKind, resolvePreviewUrl, resolveProviderWebUrl, resolveLocation } from "./AssetDetailsPanel";
import type { VideoSearchItem } from "../hooks/useVideoSearch";

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
  it("renders compact action icons before operator labels", () => {
    for (const name of ["generate", "analyze", "move", "delete", "more", "rebuild", "index", "retry"] as const) {
      const markup = renderToStaticMarkup(<AssetActionIcon name={name} />);
      expect(markup).toContain('class="asset-action-icon"');
      expect(markup).toContain('aria-hidden="true"');
    }
  });

  it("renders a friendly Drive-style preview and file properties without an internal asset", () => {
    const markup = renderToStaticMarkup(<AssetDetailsPanel item={item} metadata={metadata} onClose={noop} onPreview={noop} />);
    for (const value of ["details", "activity", item.name, "Image · image/jpeg", "31 MB", "Resolving location...", "Google Drive", "Open preview", "Open in Google Drive", "public", "indexed"]) {
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

  it("renders an in-panel TXT preview loading state instead of an image placeholder", () => {
    const text = { ...item, name: "notes.TXT", kind: "document" as const, mime_type: "text/plain" };
    const markup = renderToStaticMarkup(<AssetDetailsPanel item={text} metadata={metadata} onClose={noop} onPreview={noop} />);
    expect(markup).toContain("Loading text preview…");
    expect(markup).toContain("Text file · text/plain");
    expect(markup).toContain("Open preview");
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

  it("exposes video search AI analysis in the inspector and activity timeline", () => {
    const videoAnalysis: VideoSearchItem = {
      source_asset_id: "source-video-1", analysis_run_id: "run-1", filename: "dad.mp4",
      mime_type: "video/mp4", duration_ms: 12_000, source_type: "google_drive",
      external_source_id: "source-1", external_asset_id: "drive-video-1", web_url: null,
      thumbnail_url: null, score: 4.2,
      best_match: { start_ms: 3_000, end_ms: 6_000, summary: "A family together", visual_description: "Four people in a living room.", speech: "", confidence: 0.9, score: 4.2 },
      matches: [
        { start_ms: 3_000, end_ms: 6_000, summary: "A family together", visual_description: "Four people in a living room.", speech: "Hello", confidence: 0.9, score: 4.2 },
      ],
      steps: [
        { key: "video_analyze", label: "Video analysis", status: "completed", attempt_count: 1, max_attempts: 5, updated_at: "2026-07-27T10:00:00Z", error_code: null },
        { key: "video_search_index", label: "Video indexing", status: "completed", attempt_count: 1, max_attempts: 5, updated_at: "2026-07-27T10:01:00Z", error_code: null },
      ],
    };
    const videoItem: Asset = { ...item, id: "drive-video-1", name: "dad.mp4", kind: "video", mime_type: "video/mp4" };
    const markup = renderToStaticMarkup(<AssetDetailsPanel item={videoItem} videoAnalysis={videoAnalysis} onClose={noop} />);
    expect(markup).toContain("AI analysis");
    const analysisMarkup = renderToStaticMarkup(<VideoAnalysisDetails analysis={videoAnalysis} />);
    for (const value of ["BEST MATCH · 00:03", "A family together", "Four people in a living room.", "Speech:", "Hello", "90% confidence"]) expect(analysisMarkup).toContain(value);
    const entries = buildActivity(videoItem, null, videoAnalysis);
    expect(entries.map(entry => entry.title)).toContain("Video analysis completed");
    expect(entries.map(entry => entry.title)).toContain("Video indexing completed");
    expect(formatVideoTimestamp(65_000)).toBe("01:05");
  });

  it("builds provider-specific recreation prompts from analyzed video scenes", () => {
    const analysis: VideoSearchItem = {
      source_asset_id: "source-video-1", analysis_run_id: "run-1", filename: "embroidery.mp4",
      mime_type: "video/mp4", duration_ms: 10_000, source_type: "google_drive",
      external_source_id: "source-1", external_asset_id: "drive-video-1", web_url: null,
      thumbnail_url: null, score: 4.2,
      best_match: { start_ms: 0, end_ms: 1_000, summary: "Place an embroidery hoop", visual_description: "Hands press a white hoop onto a black sweatshirt.", speech: "", confidence: 0.95, score: 4.2 },
      matches: [
        { start_ms: 0, end_ms: 1_000, summary: "Place an embroidery hoop", visual_description: "Hands press a white hoop onto a black sweatshirt.", speech: "", confidence: 0.95, score: 4.2 },
        { start_ms: 1_000, end_ms: 4_000, summary: "Load the sweatshirt", visual_description: "The sweatshirt is inserted into an embroidery machine.", speech: "Ready", confidence: 0.95, score: 4.1 },
      ],
    };
    const prompts = buildVideoGenerationPrompts(analysis);
    expect(prompts.map(item => item.label)).toEqual(["Seedance 2.5", "Gemini Omni"]);
    expect(prompts[0].prompt).toContain("target duration of 10 seconds");
    expect(prompts[0].prompt).toContain("00:00-00:01");
    expect(prompts[1].prompt).toContain("SCENE PLAN");
    expect(prompts[1].prompt).toContain('Spoken audio: "Ready"');
    const markup = renderToStaticMarkup(<VideoGenerationPrompts analysis={analysis} />);
    for (const value of ["Recreate this video", "Seedance 2.5", "Gemini Omni", "Copy prompt", "AI draft"]) expect(markup).toContain(value);
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