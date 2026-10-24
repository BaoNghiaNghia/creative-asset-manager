import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AppliedHatPreview } from "./AppliedHatPreview";
import { chooseEmbroideryRasterSize } from "./embroidery/EmbroideryRasterQuality";

vi.mock("./embroidery/useBlueprintEmbroideryComposite", () => ({
  useBlueprintEmbroideryComposite: (src: string) => src,
}));

describe("Product embroidery design gallery", () => {
  it("applies embroidery ONLY to the front image while retaining the 2x2 image-only layout", () => {
    const html = renderToStaticMarkup(<AppliedHatPreview
      designSrc="/stage1/design-02.png" designName="THE ORIGINAL" revision="rev-02" />);
    expect((html.match(/class="rrugc-applied-hat-image"/g) || []).length).toBe(4);
    for (const stock of [
      "/rrugc/blueprint/stock/natural-black/front.jpg",
      "/rrugc/blueprint/stock/natural-black/side.jpg",
      "/rrugc/blueprint/stock/natural-black/back.jpg",
    ]) expect(html).toContain(stock);
    expect((html.match(/rrugc-applied-hat-front-artwork/g) || []).length).toBe(1);
    expect(html).not.toContain("is-side");
    expect(html).not.toContain("is-back");
    expect(html).not.toMatch(/<button\b|<small\b|<strong\b/i);
    expect(html.replace(/<[^>]*>/g, "").trim()).toBe("");
  });

  it("uses one imported, versioned color set for all three angles", () => {
    const prefix = "/api/v1/realistic-review-ugc/products/hat-stock/natural-black/";
    const stockViews = {
      front: prefix + "front?v=abc",
      side: prefix + "side?v=abc",
      back: prefix + "back?v=abc",
    };
    const html = renderToStaticMarkup(<AppliedHatPreview
      designSrc="/design.png" designName="test" revision="1" stockViews={stockViews} />);
    for (const source of Object.values(stockViews)) expect(html).toContain(source);
    expect(html).not.toContain("/rrugc/blueprint/stock/natural-black/front.jpg");
  });

  it("limits only gallery thumbnail rasterization", () => {
    expect(chooseEmbroideryRasterSize(1254, 1254, "tile")).toEqual({ width: 384, height: 384 });
    expect(chooseEmbroideryRasterSize(1254, 1254, "preview")).toEqual({ width: 1254, height: 1254 });
  });
});
