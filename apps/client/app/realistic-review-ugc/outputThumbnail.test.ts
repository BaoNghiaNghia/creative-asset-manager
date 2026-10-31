import { describe, expect, it } from "vitest";
import { stage1OutputThumbnailUrl } from "./outputThumbnail";

describe("Stage 1 lightweight gallery URLs", () => {
  it("requests small alpha-safe WebP versions without altering original output URL", () => {
    const original = "/api/v1/realistic-review-ugc/generation-jobs/stage1/job/outputs/7";
    expect(stage1OutputThumbnailUrl(original, 224))
      .toBe(original + "?thumbnail=true&size=224&preview_format=webp");
    expect(original).not.toContain("thumbnail");
  });

  it("preserves existing query arguments and bounds thumbnail resolution", () => {
    expect(stage1OutputThumbnailUrl("/api/asset?a=b", 288))
      .toBe("/api/asset?a=b&thumbnail=true&size=288&preview_format=webp");
    expect(stage1OutputThumbnailUrl("/api/asset", 5000)).toContain("size=1024&");
    expect(stage1OutputThumbnailUrl("/api/asset", 10)).toContain("size=128&");
  });
});
