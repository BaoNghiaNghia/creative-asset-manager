// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VideoSearchPlayer } from "./VideoSearchPlayer";
import type { VideoSearchItem } from "../hooks/useVideoSearch";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function item(run: string, file: string, startMs: number): VideoSearchItem {
  return { source_asset_id: "asset-" + run, analysis_run_id: run, filename: run + ".mp4", mime_type: "video/mp4", duration_ms: 100000, source_type: "google_drive", external_source_id: "source-a", external_asset_id: file, web_url: null, thumbnail_url: null, score: 1, best_match: { start_ms: startMs, end_ms: startMs + 1000, summary: run, visual_description: "", speech: "", confidence: 1, score: 1 }, matches: [] };
}

function mediaMocks(video: HTMLVideoElement, duration = 100) {
  const pause = vi.fn();
  const load = vi.fn();
  Object.defineProperty(video, "duration", { configurable: true, value: duration });
  Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: 0 });
  Object.defineProperty(video, "pause", { configurable: true, value: pause });
  Object.defineProperty(video, "load", { configurable: true, value: load });
  return { pause, load };
}

afterEach(() => document.body.replaceChildren());

describe("VideoSearchPlayer mounted lifecycle", () => {
  it("releases A without corrupting B and seeks B after metadata", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const close = vi.fn();

    await act(async () => root.render(<VideoSearchPlayer item={item("run-a", "file-a", 12000)} onClose={close} />));
    const videoA = host.querySelector("video")!;
    const a = mediaMocks(videoA);

    await act(async () => root.render(<VideoSearchPlayer item={item("run-b", "file-b", 65000)} onClose={close} />));
    const videoB = host.querySelector("video")!;
    const b = mediaMocks(videoB);

    expect(videoB).not.toBe(videoA);
    expect(a.pause).toHaveBeenCalledOnce();
    expect(a.load).toHaveBeenCalledOnce();
    expect(videoA.getAttribute("src")).toBeNull();
    expect(videoB.getAttribute("src")).toContain("/api/explorer/media/file-b");
    expect(videoB.getAttribute("src")).toContain("external_source_id=source-a");

    await act(async () => videoB.dispatchEvent(new Event("loadedmetadata")));
    expect(videoB.currentTime).toBe(65);
    expect(b.pause).not.toHaveBeenCalled();

    await act(async () => root.unmount());
    expect(b.pause).toHaveBeenCalledOnce();
    expect(b.load).toHaveBeenCalledOnce();
    expect(videoB.getAttribute("src")).toBeNull();
  });
});
