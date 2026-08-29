import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AssetGrid,
  AssetGridSkeleton,
  createThumbnailLoadQueue,
  INITIAL_HIGH_PRIORITY_THUMBNAILS,
  SEARCH_RESULT_SKELETON_COUNT,
  shouldLoadAssetThumbnail,
  thumbnailFetchPriority,
  THUMBNAIL_CONCURRENCY_LIMIT,
} from "./AssetGrid";
import { isAvifAsset } from "../utils/fileType";

describe("AVIF asset detection", () => {
  it("detects AVIF by MIME type or extension", () => {
    expect(isAvifAsset({ name: "photo.bin", mime_type: "image/avif" })).toBe(true);
    expect(isAvifAsset({ name: "PHOTO.AVIF", mime_type: "application/octet-stream" })).toBe(true);
    expect(isAvifAsset({ name: "photo.png", mime_type: "image/png" })).toBe(false);
  });
});

describe("AssetGrid thumbnail loading", () => {
  it("does not request a thumbnail until its card reaches the viewport", () => {
    expect(shouldLoadAssetThumbnail(false, "https://example.test/thumbnail.jpg")).toBe(false);
    expect(shouldLoadAssetThumbnail(true, "https://example.test/thumbnail.jpg")).toBe(true);
  });

  it("does not try to load a missing thumbnail", () => {
    expect(shouldLoadAssetThumbnail(true, undefined)).toBe(false);
    expect(shouldLoadAssetThumbnail(true, "")).toBe(false);
  });

  it("always overlays a centered play control on video cards", () => {
    const noop = () => undefined;
    const markup = renderToStaticMarkup(createElement(AssetGrid, {
      items: [{ provider: "google-drive", id: "video-1", name: "clip.mp4", kind: "video", mime_type: "video/mp4" }],
      path: [],
      selected: new Set<string>(),
      metadataByItem: {},
      onOpen: noop,
      onToggle: noop,
      onPrefetch: noop,
      onCancelPrefetch: noop,
      onPreview: noop,
      onRate: noop,
      onDetails: noop,
      onFocus: noop,
      onContextMenu: noop,
    }));
    expect(markup).toContain('class="video-thumbnail-badge"');
    expect(markup).toContain("▶");
  });
});


describe("AssetGrid thumbnail queue", () => {
  it("loads at most six thumbnails concurrently by default", () => {
    expect(THUMBNAIL_CONCURRENCY_LIMIT).toBe(6);
  });

  it("prioritizes only the first visible thumbnail batch", () => {
    expect(INITIAL_HIGH_PRIORITY_THUMBNAILS).toBe(6);
    expect(thumbnailFetchPriority(0)).toBe("high");
    expect(thumbnailFetchPriority(5)).toBe("high");
    expect(thumbnailFetchPriority(6)).toBe("auto");
  });

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
