import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./ProductsPhotoWorkflow.css", import.meta.url), "utf8");
const galleryCss = readFileSync(new URL("./AppliedHatPreview.css", import.meta.url), "utf8");

describe("Products Stage 2 photo workflow layout", () => {
  it("reserves separate columns for readable titles and shot previews", () => {
    expect(css).toContain('grid-template-areas:"title outputs" "skill controls"');
    expect(css).toMatch(/\.rrugc-products-photo-stage-title strong\{[^}]*white-space:normal/);
    expect(css).toMatch(/\.rrugc-products-photo-output-strip\{[^}]*grid-area:outputs/);
    expect(css).toMatch(/\.rrugc-products-photo-pipeline\{[^}]*min-width:0/);
    expect(css).not.toContain("min-width:790px");
  });

  it("uses full-bleed borderless completed thumbnails without changing empty slot styling", () => {
    const filled = css.match(/\.rrugc-products-photo-output-strip \.rrugc-products-photo-empty-shot\.is-completed\{([^}]*)\}/)?.[1] ?? "";
    const filledImage = css.match(/\.rrugc-products-photo-output-strip \.rrugc-products-photo-empty-shot\.is-completed>img\{([^}]*)\}/)?.[1] ?? "";
    expect(filled).toContain("padding:0");
    expect(filled).toContain("border:0");
    expect(filled).toContain("overflow:hidden");
    expect(filledImage).toContain("width:100%");
    expect(filledImage).toContain("height:100%");
    expect(filledImage).toContain("object-fit:cover");
    expect(css).toMatch(/\.rrugc-products-photo-empty-shot\{[^}]*border:1px dashed/);
    expect(css).toContain(".rrugc-products-photo-empty-shot.is-completed:focus-visible");
  });

  it("shows four equal embroidery gallery tiles without responsive reflow to three columns", () => {
    expect(galleryCss).toMatch(/grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
    expect(galleryCss).not.toMatch(/repeat\(3/);
    expect(css).toContain(">.rrugc-applied-hat-grid");
  });

  it("stacks all workflow controls on narrow screens without hiding outputs", () => {
    expect(css).toContain("@media(max-width:700px)");
    expect(css).toContain('grid-template-areas:"title" "outputs" "skill" "controls"');
    expect(css).toContain("overflow-x:auto");
  });
});
