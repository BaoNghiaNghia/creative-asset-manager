import { describe, expect, it } from "vitest";
import { publicHatStockUrl, stage2OriginalUrl, stage2PinterestThumbnailUrl, stage2ThumbnailUrl } from "./stage2Media";

describe("GatorHats Stage 2 media authorization", () => {
  it("uses authenticated small previews only for supported routes", () => {
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/colorways/one/output", 128)).toContain("thumbnail=true&size=128");
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/source-plans/one/image?thumbnail=true&size=1024", 128)).toContain("thumbnail=true&size=128");
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output", 128)).toBe("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output?thumbnail=true&size=128");
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output?version=3", 256)).toBe("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output?version=3&thumbnail=true&size=256");
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/stage2-jobs/one/reference-image?reference_index=2", 192)).toContain("thumbnail=true&size=192");
    expect(stage2OriginalUrl("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output?version=3&thumbnail=true&size=128")).toBe("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output?version=3");
    expect(stage2OriginalUrl("/api/v1/realistic-review-ugc/source-plans/id/image?thumbnail=true&size=256&v=abc")).toBe("/api/v1/realistic-review-ugc/source-plans/id/image?v=abc");
    expect(stage2OriginalUrl("https://other.example.com/x.jpg?thumbnail=true&size=256")).toBe("https://other.example.com/x.jpg?thumbnail=true&size=256");
    expect(stage2ThumbnailUrl("https://other.example/stock.png", 128)).toBe("https://other.example/stock.png");
  });
  it("requests actual Pinterest CDN thumbnails and never rewrites other origins", () => {
    expect(stage2PinterestThumbnailUrl("https://i.pinimg.com/originals/aa/bb/cc/image.jpg")).toBe("https://i.pinimg.com/236x/aa/bb/cc/image.jpg");
    expect(stage2PinterestThumbnailUrl("https://i.pinimg.com/736x/aa/bb/cc/image.png")).toBe("https://i.pinimg.com/236x/aa/bb/cc/image.png");
    expect(stage2PinterestThumbnailUrl("https://evil.example/path/originals/image.jpg")).toBe("https://evil.example/path/originals/image.jpg");
    expect(stage2PinterestThumbnailUrl("https://i.pinimg.com.evil.example/originals/image.jpg")).toBe("https://i.pinimg.com.evil.example/originals/image.jpg");
    expect(stage2PinterestThumbnailUrl("/api/v1/realistic-review-ugc/colorways/id/output")).toBe("/api/v1/realistic-review-ugc/colorways/id/output");
  });
  it("never routes authenticated URLs or arbitrary URLs to public CDN", () => {
    expect(publicHatStockUrl("/api/v1/realistic-review-ugc/colorways/one/output", "https://static.example.com")).toBe("/api/v1/realistic-review-ugc/colorways/one/output");
    expect(publicHatStockUrl("/rrugc/blueprint/stock/natural-black/front.jpg", "https://static.example.com")).toBe("https://static.example.com/rrugc/blueprint/stock/natural-black/front.jpg");
    expect(publicHatStockUrl("/rrugc/blueprint/stock/natural-black/front.jpg", "http://static.example.com")).toBe("/rrugc/blueprint/stock/natural-black/front.jpg");
    expect(publicHatStockUrl("//other.example/front.jpg", "https://static.example.com")).toBe("//other.example/front.jpg");
  });
});
