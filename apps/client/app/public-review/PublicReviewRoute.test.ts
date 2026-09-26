import { afterEach, describe, expect, it, vi } from "vitest";
import { autoplayReviewVideo, formatReviewDuration, localPublicSearchSuggestions, mergePublicSearchSuggestions, publicFolderIdFromPath, publicReviewLocationFromPath, publicShareIdFromPath, reviewAspectRatio, reviewFolderPath, reviewMediaPosition, reviewShareUrl } from "./PublicReviewRoute";
import type { Asset } from "./api";

describe("reviewShareUrl", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the bearer key in the fragment and encodes it", () => {
    vi.stubGlobal("location", { origin: "https://creative-assets.example" });
    expect(reviewShareUrl("share-id", "key+/=?")).toBe(
      "https://creative-assets.example/share/share-id#key=key%2B%2F%3D%3F",
    );
    expect(reviewShareUrl("share-id", "key+/=?", "folder_A-1")).toBe(
      "https://creative-assets.example/share/share-id/folder/folder_A-1#key=key%2B%2F%3D%3F",
    );
  });
});

describe("public review folder routes", () => {
  it("parses root and folder routes and safely encodes folder IDs", () => {
    expect(publicReviewLocationFromPath("/share/share-id")).toEqual({ publicId: "share-id", folderId: null });
    expect(publicReviewLocationFromPath("/share/share-id/folder/folder_A-1")).toEqual({ publicId: "share-id", folderId: "folder_A-1" });
    expect(publicShareIdFromPath("/share/share-id/folder/folder_A-1")).toBe("share-id");
    expect(publicFolderIdFromPath("/share/share-id/folder/folder%20name")).toBe("folder name");
    expect(reviewFolderPath("share-id", "folder name")).toBe("/share/share-id/folder/folder%20name");
    expect(publicReviewLocationFromPath("/share/share-id/unknown/folder")).toBeNull();
  });
});



describe("public review search suggestions", () => {
  const asset = (filename: string): Asset => ({
    kind: "asset",
    asset_id: filename,
    source_asset_id: "source-" + filename,
    filename,
    media_type: "image/jpeg",
    thumbnail_url: "/thumbnail/" + filename,
    preview_url: "/preview/" + filename,
  });

  it("shows immediate filename recommendations before remote suggestions arrive", () => {
    expect(localPublicSearchSuggestions([
      asset("summer-cat-banner.jpg"),
      asset("cat-campaign.png"),
      asset("other.jpg"),
    ], "cat").map(value => value.text)).toEqual([
      "summer-cat-banner.jpg",
      "cat-campaign.png",
    ]);
  });

  it("prefers backend recommendations, deduplicates, and keeps local fallback", () => {
    const remote = [{ text: "cat campaign", prefix: "cat", completion: " campaign", kind: "visible_text" as const }];
    const local = localPublicSearchSuggestions([asset("cat-campaign.png"), asset("cat campaign")], "cat");
    expect(mergePublicSearchSuggestions(remote, local, "cat").map(value => value.text)).toEqual([
      "cat campaign",
      "cat-campaign.png",
    ]);
  });
});

describe("reviewMediaPosition", () => {
  const asset = (asset_id: string, media_type: string): Asset => ({
    kind: "asset",
    asset_id,
    source_asset_id: "source-" + asset_id,
    filename: asset_id + (media_type.startsWith("video/") ? ".mp4" : ".jpg"),
    media_type,
    thumbnail_url: "/thumbnail/" + asset_id,
    preview_url: "/preview/" + asset_id,
  });

  it("keeps mixed images and videos in the exact navigation order", () => {
    const items = [
      asset("image-1", "image/jpeg"),
      asset("video-1", "video/mp4"),
      asset("image-2", "image/png"),
      asset("video-2", "video/webm"),
    ];

    expect(reviewMediaPosition(items, items[2])).toEqual({
      keys: [
        "image-1:source-image-1",
        "video-1:source-video-1",
        "image-2:source-image-2",
        "video-2:source-video-2",
      ],
      currentIndex: 2,
    });
    expect(reviewMediaPosition(items, items[0]).currentIndex).toBe(0);
    expect(reviewMediaPosition(items, items[3]).currentIndex).toBe(3);
  });
});

describe("review media metadata", () => {
  it("formats common review aspect ratios", () => {
    expect(reviewAspectRatio(1920, 1080)).toBe("16:9");
    expect(reviewAspectRatio(1080, 1920)).toBe("9:16");
    expect(reviewAspectRatio(1080, 1080)).toBe("1:1");
    expect(reviewAspectRatio(1000, 562)).toBe("16:9");
    expect(reviewAspectRatio(0, 1080)).toBe("—");
  });

  it("formats media duration compactly", () => {
    expect(formatReviewDuration(10.4)).toBe("00:10");
    expect(formatReviewDuration(65)).toBe("01:05");
    expect(formatReviewDuration(3661)).toBe("1:01:01");
    expect(formatReviewDuration(undefined)).toBe("—");
  });
});

describe("autoplayReviewVideo", () => {
  it("plays the current video and falls back when the browser blocks autoplay", async () => {
    const video = { play: vi.fn().mockResolvedValue(undefined), pause: vi.fn() } as unknown as HTMLVideoElement;
    expect(await autoplayReviewVideo(video, () => true)).toBe(true);
    expect(video.play).toHaveBeenCalledOnce();

    vi.mocked(video.play).mockRejectedValueOnce(new DOMException("blocked", "NotAllowedError"));
    expect(await autoplayReviewVideo(video, () => true)).toBe(false);
    expect(video.pause).not.toHaveBeenCalled();
  });

  it("never starts an inactive video and pauses a video that becomes stale while starting", async () => {
    let finishPlay!: () => void;
    const video = { play: vi.fn().mockImplementation(() => new Promise<void>(resolve => { finishPlay = resolve; })), pause: vi.fn() } as unknown as HTMLVideoElement;
    expect(await autoplayReviewVideo(video, () => false)).toBe(false);
    expect(video.play).not.toHaveBeenCalled();

    let current = true;
    const attempt = autoplayReviewVideo(video, () => current);
    current = false;
    finishPlay();
    expect(await attempt).toBe(false);
    expect(video.pause).toHaveBeenCalledOnce();
  });
});
