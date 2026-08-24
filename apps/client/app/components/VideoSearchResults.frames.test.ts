// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { captureVideoSequenceFrames } from "./VideoSearchResults";

describe("video sequence frame capture", () => {
  it("seeks once through the source and returns a distinct frame for every segment timestamp", async () => {
    const video = document.createElement("video");
    const canvas = document.createElement("canvas");
    let currentTime = 0;
    let drawnTime = 0;
    const seeks: number[] = [];

    Object.defineProperties(video, {
      duration: { configurable: true, value: 10 },
      readyState: { configurable: true, value: 2 },
      videoWidth: { configurable: true, value: 320 },
      videoHeight: { configurable: true, value: 180 },
      currentTime: {
        configurable: true,
        get: () => currentTime,
        set: (value: number) => {
          currentTime = value;
          seeks.push(value);
          queueMicrotask(() => video.dispatchEvent(new Event("seeked")));
        },
      },
      load: {
        configurable: true,
        value: vi.fn(() => queueMicrotask(() => video.dispatchEvent(new Event("loadedmetadata")))),
      },
      pause: { configurable: true, value: vi.fn() },
    });
    Object.defineProperty(canvas, "getContext", {
      configurable: true,
      value: () => ({ drawImage: (source: HTMLVideoElement) => { drawnTime = source.currentTime; } }),
    });
    Object.defineProperty(canvas, "toDataURL", {
      configurable: true,
      value: () => "data:image/jpeg;time=" + drawnTime,
    });

    const frames = await captureVideoSequenceFrames(
      "/api/explorer/media/file-a",
      [0, 2000, 4000],
      new AbortController().signal,
      { video, canvas },
    );

    expect(frames).toEqual([
      "data:image/jpeg;time=0",
      "data:image/jpeg;time=2",
      "data:image/jpeg;time=4",
    ]);
    expect(seeks).toEqual([2, 4]);
    expect(video.pause).toHaveBeenCalledOnce();
    expect(video.getAttribute("src")).toBeNull();
  });
});
