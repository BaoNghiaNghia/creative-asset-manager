// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { VIDEO_HOVER_PREVIEW_DELAY_MS, VideoSearchResults } from "./VideoSearchResults";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const item: VideoSearchItem = {
  source_asset_id: "asset-a", analysis_run_id: "run-a", filename: "ride.mp4", mime_type: "video/mp4",
  duration_ms: 30000, source_type: "google_drive", external_source_id: "source-a", external_asset_id: "file-a",
  web_url: null, thumbnail_url: null, score: 1,
  best_match: { start_ms: 12000, end_ms: 18000, summary: "horse", visual_description: "", speech: "", confidence: 1, score: 1 },
  matches: [],
};

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("VideoSearchResults hover preview", () => {
  it("opens only after four seconds and streams the best matching segment", async () => {
    vi.useFakeTimers();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<VideoSearchResults items={[item]} onOpen={() => undefined} onDetails={() => undefined} />));
    const card = host.querySelector<HTMLElement>(".video-search-card")!;
    Object.defineProperty(card, "getBoundingClientRect", { configurable: true, value: () => ({ left: 20, top: 500, width: 320 }) });

    await act(async () => card.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    await act(async () => vi.advanceTimersByTime(VIDEO_HOVER_PREVIEW_DELAY_MS - 1));
    expect(document.querySelector(".video-hover-preview")).toBeNull();

    await act(async () => vi.advanceTimersByTime(1));
    const preview = document.querySelector(".video-hover-preview")!;
    const video = preview.querySelector("video")!;
    expect(preview.getAttribute("aria-label")).toContain("ride.mp4");
    expect(video.getAttribute("src")).toContain("/api/explorer/media/file-a");
    expect(video.dataset.previewStartSeconds).toBe("12");
    expect(video.dataset.previewEndSeconds).toBe("18");
    expect(video.muted).toBe(true);

    await act(async () => root.unmount());
  });

  it("cancels the delayed preview when the pointer leaves early", async () => {
    vi.useFakeTimers();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<VideoSearchResults items={[item]} onOpen={() => undefined} onDetails={() => undefined} />));
    const card = host.querySelector<HTMLElement>(".video-search-card")!;
    await act(async () => card.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    await act(async () => card.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })));
    await act(async () => vi.advanceTimersByTime(VIDEO_HOVER_PREVIEW_DELAY_MS));
    expect(document.querySelector(".video-hover-preview")).toBeNull();
    await act(async () => root.unmount());
  });
});
