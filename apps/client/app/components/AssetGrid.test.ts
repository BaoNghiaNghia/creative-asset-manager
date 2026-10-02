import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AssetGrid,
  AssetGridSkeleton,
  assetIdsInSelectionRectangle,
  originalAssetDragPayload,
  nativeOriginalDragItems,
  nativeOriginalDragKey,
  nativeOriginalDragMode,
  nativeOriginalPrewarmItems,
  isAdditiveSelectionClick,
  explorerSelectionForClick,
  createThumbnailLoadQueue,
  INITIAL_HIGH_PRIORITY_THUMBNAILS,
  SEARCH_RESULT_SKELETON_COUNT,
  shouldLoadAssetThumbnail,
  shouldRetryVideoThumbnail,
  thumbnailFetchPriority,
  thumbnailRetryUrl,
  THUMBNAIL_CONCURRENCY_LIMIT,
  VIDEO_THUMBNAIL_RETRY_DELAYS_MS,
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

  it("retries thumbnails only for recently uploaded videos with bounded backoff", () => {
    const now = Date.parse("2026-09-29T10:00:00Z");
    const recentVideo = { kind: "video" as const, modified_at: "2026-09-29T09:59:30Z" };
    expect(VIDEO_THUMBNAIL_RETRY_DELAYS_MS).toEqual([10_000, 15_000, 25_000, 40_000]);
    expect(shouldRetryVideoThumbnail(recentVideo, 0, now)).toBe(true);
    expect(shouldRetryVideoThumbnail(recentVideo, VIDEO_THUMBNAIL_RETRY_DELAYS_MS.length, now)).toBe(false);
    expect(shouldRetryVideoThumbnail({ kind: "video", modified_at: "2026-09-29T09:40:00Z" }, 0, now)).toBe(false);
    expect(shouldRetryVideoThumbnail({ kind: "image", modified_at: recentVideo.modified_at }, 0, now)).toBe(false);
  });

  it("cache-busts only retry thumbnail requests", () => {
    expect(thumbnailRetryUrl("/api/explorer/thumbnail/a?provider=google-drive", 0)).toBe("/api/explorer/thumbnail/a?provider=google-drive");
    expect(thumbnailRetryUrl("/api/explorer/thumbnail/a?provider=google-drive", 2)).toBe("/api/explorer/thumbnail/a?provider=google-drive&thumbnail_retry=2");
  });

  it("always overlays a centered play control on video cards", () => {
    const noop = () => undefined;
    const markup = renderToStaticMarkup(createElement(AssetGrid, {
      items: [{ provider: "google-drive", id: "video-1", name: "clip.mp4", kind: "video", mime_type: "video/mp4" }],
      path: [],
      selected: new Set<string>(),
      metadataByItem: {},
      onOpen: noop,
      onReplaceSelection: noop,
      onPrefetch: noop,
      onCancelPrefetch: noop,
      onPreview: noop,
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


describe("AssetGrid marquee selection and drag-out", () => {
  it("selects every card intersecting a drag rectangle", () => {
    const ids = assetIdsInSelectionRectangle(
      { startX: 10, startY: 10, currentX: 90, currentY: 90 },
      [
        { id: "inside", rect: { left: 20, top: 20, right: 60, bottom: 60 } },
        { id: "edge", rect: { left: 80, top: 80, right: 120, bottom: 120 } },
        { id: "outside", rect: { left: 91, top: 10, right: 130, bottom: 50 } },
      ],
    );
    expect(ids).toEqual(["inside", "edge"]);
  });

  it("prepares original media URLs for an external native drag without browser drop data", () => {
    const payload = originalAssetDragPayload([
      { provider: "google-drive", id: "asset-1", name: "photo.jpg", kind: "image", mime_type: "image/jpeg", external_source_id: "source-1" },
      { provider: "google-drive", id: "asset-2", name: "clip.mp4", kind: "video", mime_type: "video/mp4", external_source_id: "source-1" },
    ], "https://creative-assets.example");
    expect(payload.downloadUrl).toBe("image/jpeg:photo.jpg:https://creative-assets.example/api/explorer/media/asset-1?provider=google-drive&external_source_id=source-1");
    expect(payload.uriList).toContain("/media/asset-1?");
    expect(payload.uriList).toContain("/media/asset-2?");
    expect(payload.sourceIds).toBe('["asset-1","asset-2"]');
  });

  it("maps desktop native drag to original asset identity, never thumbnail bytes", () => {
    const items = nativeOriginalDragItems([
      {
        provider: "google-drive",
        id: "asset-1",
        name: "photo.CR3",
        kind: "image",
        mime_type: "image/x-canon-cr3",
        external_source_id: "source-1",
        modified_at: "2026-09-22T08:00:00Z",
        size: 123456,
        thumbnail_url: "/api/explorer/thumbnail/asset-1",
      },
    ]);
    expect(items).toEqual([{
      id: "asset-1",
      name: "photo.CR3",
      mimeType: "image/x-canon-cr3",
      provider: "google-drive",
      externalSourceId: "source-1",
      modifiedAt: "2026-09-22T08:00:00Z",
      size: 123456,
    }]);
    expect(JSON.stringify(items)).not.toContain("thumbnail");
  });

  it("keys prepared native drag tickets by original identity and version hints", () => {
    const base = nativeOriginalDragKey([{
      id: "asset-1",
      name: "photo.jpg",
      mimeType: "image/jpeg",
      provider: "google-drive",
      externalSourceId: "source-1",
      modifiedAt: "2026-09-22T08:00:00Z",
      size: 5,
    }]);
    const changed = nativeOriginalDragKey([{
      id: "asset-1",
      name: "photo.jpg",
      mimeType: "image/jpeg",
      provider: "google-drive",
      externalSourceId: "source-1",
      modifiedAt: "2026-09-22T09:00:00Z",
      size: 5,
    }]);
    expect(changed).not.toBe(base);
  });

  it("prewarms the complete native drag request and prioritizes the dragged item", () => {
    const mib = 1024 * 1024;
    const items = [
      { provider: "google-drive", id: "a", name: "a.jpg", kind: "image", mime_type: "image/jpeg", size: 120 * mib },
      { provider: "google-drive", id: "b", name: "b.jpg", kind: "image", mime_type: "image/jpeg", size: 40 * mib },
      { provider: "google-drive", id: "c", name: "c.jpg", kind: "image", mime_type: "image/jpeg", size: 40 * mib },
      { provider: "google-drive", id: "d", name: "d.jpg", kind: "image", mime_type: "image/jpeg", size: 40 * mib },
      { provider: "google-drive", id: "huge", name: "huge.mov", kind: "video", mime_type: "video/quicktime", size: 800 * mib },
      { provider: "google-drive", id: "unknown", name: "unknown.jpg", kind: "image", mime_type: "image/jpeg" },
    ] as const;
    const prewarm = nativeOriginalPrewarmItems([...items], "c");
    expect(prewarm.map(item => item.id)).toEqual(["c", "a", "b", "d", "huge", "unknown"]);
  });

  it("hides the Discovered processing badge on folder cards", () => {
    const noop = () => undefined;
    const markup = renderToStaticMarkup(createElement(AssetGrid, {
      items: [
        { provider: "google-drive", id: "folder-1", name: "Amazon - Collection", kind: "folder", mime_type: "application/vnd.google-apps.folder" },
        { provider: "google-drive", id: "file-1", name: "photo.jpg", kind: "image", mime_type: "image/jpeg" },
      ],
      path: [],
      selected: new Set<string>(),
      metadataByItem: {
        "folder-1": { item_id: "folder-1", tag_ids: [], rating: null, processing_status: "discovered" },
        "file-1": { item_id: "file-1", tag_ids: [], rating: null, processing_status: "discovered" },
      },
      onOpen: noop,
      onReplaceSelection: noop,
      onPrefetch: noop,
      onCancelPrefetch: noop,
      onPreview: noop,
      onDetails: noop,
      onFocus: noop,
      onContextMenu: noop,
    }));
    const folderMarkup = markup.slice(
      markup.indexOf('data-asset-id="folder-1"'),
      markup.indexOf('data-asset-id="file-1"'),
    );
    expect(folderMarkup).not.toContain("Discovered");
    expect(markup).toContain("Discovered");
  });

  it("marks files, but not folders, as draggable originals", () => {
    const noop = () => undefined;
    const markup = renderToStaticMarkup(createElement(AssetGrid, {
      items: [
        { provider: "google-drive", id: "file-1", name: "photo.jpg", kind: "image", mime_type: "image/jpeg" },
        { provider: "google-drive", id: "folder-1", name: "Folder", kind: "folder", mime_type: "application/vnd.google-apps.folder" },
      ],
      path: [],
      selected: new Set<string>(),
      metadataByItem: {},
      onOpen: noop,
      onReplaceSelection: noop,
      onPrefetch: noop,
      onCancelPrefetch: noop,
      onPreview: noop,
      onDetails: noop,
      onFocus: noop,
      onContextMenu: noop,
    }));
    expect(markup).toContain('data-asset-id="file-1" draggable="true"');
    expect(markup).toContain('data-asset-id="folder-1" draggable="false"');
  });
});

describe("AssetGrid native drag strategy", () => {
  it("uses native drag only for a synchronously prepared ticket", () => {
    expect(nativeOriginalDragMode(true, true, true)).toBe("prepared");
    expect(nativeOriginalDragMode(true, false, true)).toBe("web");
    expect(nativeOriginalDragMode(true, false, false)).toBe("web");
    expect(nativeOriginalDragMode(false, false, true)).toBe("web");
  });
});

describe("AssetGrid Explorer-style selection", () => {
  const ids = ["a", "b", "c", "d", "e"];

  it("uses Ctrl+click on Windows and Cmd+click on macOS to toggle an item", () => {
    expect(isAdditiveSelectionClick({ ctrlKey: true, metaKey: false })).toBe(true);
    expect(isAdditiveSelectionClick({ ctrlKey: false, metaKey: true })).toBe(true);
    expect(isAdditiveSelectionClick({ ctrlKey: false, metaKey: false })).toBe(false);
  });

  it("plain click replaces the selection with exactly one item", () => {
    const result = explorerSelectionForClick(ids, new Set(["a", "c"]), "d", "c", {
      ctrlKey: false, metaKey: false, shiftKey: false,
    });
    expect([...result.selected]).toEqual(["d"]);
    expect(result.anchorId).toBe("d");
  });

  it("Ctrl click toggles one item without clearing the rest", () => {
    const added = explorerSelectionForClick(ids, new Set(["a"]), "c", "a", {
      ctrlKey: true, metaKey: false, shiftKey: false,
    });
    expect([...added.selected]).toEqual(["a", "c"]);

    const removed = explorerSelectionForClick(ids, added.selected, "a", "c", {
      ctrlKey: true, metaKey: false, shiftKey: false,
    });
    expect([...removed.selected]).toEqual(["c"]);
  });

  it("Shift click selects the contiguous range from the anchor", () => {
    const result = explorerSelectionForClick(ids, new Set(["b"]), "e", "b", {
      ctrlKey: false, metaKey: false, shiftKey: true,
    });
    expect([...result.selected]).toEqual(["b", "c", "d", "e"]);
    expect(result.anchorId).toBe("b");
  });

  it("Ctrl+Shift adds a range to the existing selection", () => {
    const result = explorerSelectionForClick(ids, new Set(["a"]), "e", "c", {
      ctrlKey: true, metaKey: false, shiftKey: true,
    });
    expect([...result.selected]).toEqual(["a", "c", "d", "e"]);
  });
});