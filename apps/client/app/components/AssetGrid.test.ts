import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AssetGridSkeleton,
  createThumbnailLoadQueue,
  SEARCH_RESULT_SKELETON_COUNT,
  shouldLoadAssetThumbnail,
} from "./AssetGrid";

describe("AssetGrid thumbnail loading", () => {
  it("does not request a thumbnail until its card reaches the viewport", () => {
    expect(shouldLoadAssetThumbnail(false, "https://example.test/thumbnail.jpg")).toBe(false);
    expect(shouldLoadAssetThumbnail(true, "https://example.test/thumbnail.jpg")).toBe(true);
  });

  it("does not try to load a missing thumbnail", () => {
    expect(shouldLoadAssetThumbnail(true, undefined)).toBe(false);
    expect(shouldLoadAssetThumbnail(true, "")).toBe(false);
  });
});


describe("AssetGrid thumbnail queue", () => {
  it("limits concurrent thumbnail requests and starts the next queued image after release", () => {
    const queue = createThumbnailLoadQueue(2);
    const started: string[] = [];
    const first = queue.acquire(() => started.push("first"));
    const second = queue.acquire(() => started.push("second"));
    queue.acquire(() => started.push("third"));

    expect(started).toEqual(["first", "second"]);
    expect(queue.activeCount()).toBe(2);
    expect(queue.pendingCount()).toBe(1);

    first.release();

    expect(started).toEqual(["first", "second", "third"]);
    expect(queue.activeCount()).toBe(2);
    second.release();
  });

  it("removes an offscreen queued thumbnail without starting its request", () => {
    const queue = createThumbnailLoadQueue(1);
    const started: string[] = [];
    const first = queue.acquire(() => started.push("first"));
    const queued = queue.acquire(() => started.push("queued"));

    queued.cancel();
    first.release();

    expect(started).toEqual(["first"]);
    expect(queue.activeCount()).toBe(0);
    expect(queue.pendingCount()).toBe(0);
  });
});


describe("AssetGrid search skeleton", () => {
  it("uses a bounded skeleton grid while results are loading", () => {
    const markup = renderToStaticMarkup(createElement(AssetGridSkeleton));
    expect(SEARCH_RESULT_SKELETON_COUNT).toBe(18);
    expect(markup).toContain('aria-label="Loading search results"');
    expect((markup.match(/asset-card-skeleton-preview/g) || []).length).toBe(18);
  });
});
