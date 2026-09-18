import { describe, expect, it } from "vitest";
import { createPublicThumbnailQueue, usesPublicThumbnail } from "./PublicAssetThumbnail";

describe("public thumbnail queue", () => {
  it("limits concurrent media loads and drains in request order", () => {
    const queue = createPublicThumbnailQueue(2);
    const started: string[] = [];

    const first = queue.acquire(() => started.push("first"));
    const second = queue.acquire(() => started.push("second"));
    const third = queue.acquire(() => started.push("third"));

    expect(started).toEqual(["first", "second"]);
    expect(queue.activeCount()).toBe(2);
    expect(queue.pendingCount()).toBe(1);

    first.release();

    expect(started).toEqual(["first", "second", "third"]);
    expect(queue.activeCount()).toBe(2);
    expect(queue.pendingCount()).toBe(0);

    second.release();
    third.release();
    expect(queue.activeCount()).toBe(0);
  });

  it("removes a queued load when its card unmounts", () => {
    const queue = createPublicThumbnailQueue(1);
    const first = queue.acquire(() => {});
    const second = queue.acquire(() => { throw new Error("cancelled work must not start"); });

    second.cancel();
    first.release();

    expect(queue.activeCount()).toBe(0);
    expect(queue.pendingCount()).toBe(0);
  });
});

describe("public thumbnail eligibility", () => {
  it("uses the authorized thumbnail proxy for image and video cards", () => {
    const image = { media_type: "image/jpeg", filename: "image.jpg" };
    const video = { media_type: "application/octet-stream", filename: "clip.mp4" };
    const file = { media_type: "application/pdf", filename: "brief.pdf" };

    expect(usesPublicThumbnail(image as never)).toBe(true);
    expect(usesPublicThumbnail(video as never)).toBe(true);
    expect(usesPublicThumbnail(file as never)).toBe(false);
  });
});
