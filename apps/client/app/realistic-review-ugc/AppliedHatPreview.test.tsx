import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AppliedHatPreview } from "./AppliedHatPreview";
import { chooseEmbroideryRasterSize } from "./embroidery/EmbroideryRasterQuality";

vi.mock("./embroidery/useBlueprintEmbroideryComposite", () => ({
  useBlueprintEmbroideryComposite: (src: string) => src,
}));

describe("Product embroidery design gallery", () => {
  it("renders artwork plus three automatically embroidered hats, with no visible controls or descriptions", () => {
    const markup = renderToStaticMarkup(
      <AppliedHatPreview
        designSrc="/stage1/design-02.png"
        designName="THE ORIGINAL"
        revision="rev-02"
      />,
    );
    expect((markup.match(/class="rrugc-applied-hat-image"/g) || []).length).toBe(4);
    expect(markup).toContain('src="/stage1/design-02.png"');
    expect(markup).toContain('src="/rrugc/blueprint/fronts/natural-black.webp"');
    expect(markup).toContain('src="/rrugc/blueprint/stock/natural-black/side.jpg"');
    expect(markup).toContain('src="/rrugc/blueprint/stock/natural-black/back.jpg"');
    for (const view of ["front", "side", "back"]) {
      expect(markup).toContain(`rrugc-applied-hat-artwork is-${view}`);
      expect(markup).toContain(`${view === "side" ? "right side" : view} view with embroidery design applied`);
    }
    expect(markup).not.toMatch(/<button\b|<small\b|<strong\b/i);
    expect(markup.replace(/<[^>]*>/g, "").trim()).toBe("");
  });

  it("limits tile compositing cost without changing standard previews", () => {
    expect(chooseEmbroideryRasterSize(1254, 1254, "tile")).toEqual({ width: 384, height: 384 });
    expect(chooseEmbroideryRasterSize(1254, 1254, "preview")).toEqual({ width: 1254, height: 1254 });
  });
});
