import { describe, expect, it } from "vitest";
import { publicHatStockUrl, stage2ThumbnailUrl } from "./stage2Media";

describe("GatorHats Stage 2 media authorization", () => {
  it("uses authenticated small previews only for supported routes", () => {
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/colorways/one/output", 128)).toContain("thumbnail=true&size=128");
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/source-plans/one/image?thumbnail=true&size=1024", 128)).toContain("thumbnail=true&size=128");
    expect(stage2ThumbnailUrl("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output", 128)).toBe("/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/id/output");
    expect(stage2ThumbnailUrl("https://other.example/stock.png", 128)).toBe("https://other.example/stock.png");
  });
  it("never routes authenticated URLs or arbitrary URLs to public CDN", () => {
    expect(publicHatStockUrl("/api/v1/realistic-review-ugc/colorways/one/output", "https://static.example.com")).toBe("/api/v1/realistic-review-ugc/colorways/one/output");
    expect(publicHatStockUrl("/rrugc/blueprint/stock/natural-black/front.jpg", "https://static.example.com")).toBe("https://static.example.com/rrugc/blueprint/stock/natural-black/front.jpg");
    expect(publicHatStockUrl("/rrugc/blueprint/stock/natural-black/front.jpg", "http://static.example.com")).toBe("/rrugc/blueprint/stock/natural-black/front.jpg");
    expect(publicHatStockUrl("//other.example/front.jpg", "https://static.example.com")).toBe("//other.example/front.jpg");
  });
});
