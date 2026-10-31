import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AppliedHatPreview } from "./AppliedHatPreview";
import { chooseEmbroideryRasterSize } from "./embroidery/EmbroideryRasterQuality";

vi.mock("./embroidery/useBlueprintEmbroideryComposite", () => ({
  useBlueprintEmbroideryComposite: (src: string) => src,
}));

// The real DeferredImage intentionally withholds src until observed in the
// viewport. Keep this component test focused on the stock URLs supplied to it.
vi.mock("./DeferredImage", () => ({
  DeferredImage: ({ src, className = "" }: { src: string; className?: string }) => (
    <img src={src} className={className} alt="" />
  ),
}));

describe("Product embroidery design gallery", () => {
  it("applies embroidery ONLY to the front image while retaining the five-tile image-only layout", () => {
    const html = renderToStaticMarkup(<AppliedHatPreview
      designSrc="/stage1/design-02.png" designName="THE ORIGINAL" revision="rev-02" />);
    expect((html.match(/class="rrugc-applied-hat-image(?: is-original-design)?"/g) || []).length).toBe(5);
    for (const stock of [
      "/rrugc/blueprint/stock/natural-black/front.jpg",
      "/rrugc/blueprint/stock/natural-black/side.jpg",
      "/rrugc/blueprint/stock/natural-black/back.jpg",
    ]) expect(html).toContain(stock);
    expect(html).toContain('3/4 photo not synced');
    expect((html.match(/rrugc-applied-hat-front-artwork/g) || []).length).toBe(1);
    expect(html).not.toContain("is-side");
    expect(html).not.toContain("is-back");
    expect(html).not.toMatch(/<button\b|<small\b|<strong\b/i);
    expect(html.replace(/<[^>]*>/g, "").trim()).toBe("3/4 photo not synced");
  });

  it("shows the alpha-preserving thread PNG in both design and front previews, not a black Drive thumbnail", () => {
    const source = "/api/v1/realistic-review-ugc/source-plans/plan-1/image?thumbnail=true&size=128&v=rev123";
    const html = renderToStaticMarkup(<AppliedHatPreview
      designSrc={source} designName="THE ORIGINAL" revision="rev123" />);
    const original = "/api/v1/realistic-review-ugc/source-plans/plan-1/image?thumbnail=true&amp;size=384&amp;v=rev123";
    const threads = original + "&amp;artwork=stage1-thread-geometry-v1";
    expect(html).not.toContain('src="' + original + '"');
    expect((html.match(/src="\/api\/v1\/realistic-review-ugc\/source-plans\/plan-1\/image\?thumbnail=true&amp;size=384&amp;v=rev123&amp;artwork=stage1-thread-geometry-v1"/g) || [])).toHaveLength(2);
    expect(html).toContain('src="' + threads + '"');
    expect(html).not.toContain("stage1-source-alpha-v2");
    expect(html).toContain('class="rrugc-applied-hat-image is-original-design"');
    expect(html).not.toContain("size=128");
    // Only the front view should receive the artwork; side/back remain unmodified stock.
    expect((html.match(/rrugc-applied-hat-front-artwork/g) || [])).toHaveLength(1);
  });

  it("uses one imported, versioned color set for all four angles", () => {
    const prefix = "/api/v1/realistic-review-ugc/products/hat-stock/natural-black/";
    const stockViews = {
      front: prefix + "front?v=abc",
      side: prefix + "side?v=abc",
      back: prefix + "back?v=abc",
      "three-quarter": prefix + "three-quarter?v=abc",
    };
    const html = renderToStaticMarkup(<AppliedHatPreview
      designSrc="/design.png" designName="test" revision="1" stockViews={stockViews} />);
    for (const source of Object.values(stockViews)) expect(html).toContain(source);
    expect(html).not.toContain("/rrugc/blueprint/stock/natural-black/front.jpg");
    expect(html).not.toContain("3/4 photo not synced");
  });

  it("limits only gallery thumbnail rasterization", () => {
    expect(chooseEmbroideryRasterSize(1254, 1254, "tile")).toEqual({ width: 384, height: 384 });
    expect(chooseEmbroideryRasterSize(1254, 1254, "preview")).toEqual({ width: 1254, height: 1254 });
  });
});