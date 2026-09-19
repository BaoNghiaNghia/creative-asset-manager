import { afterEach, describe, expect, it, vi } from "vitest";
import { autoplayReviewVideo, reviewShareUrl } from "./PublicReviewRoute";

describe("reviewShareUrl", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the bearer key in the fragment and encodes it", () => {
    vi.stubGlobal("location", { origin: "https://creative-assets.example" });
    expect(reviewShareUrl("share-id", "key+/=?")).toBe(
      "https://creative-assets.example/share/share-id#key=key%2B%2F%3D%3F",
    );
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
