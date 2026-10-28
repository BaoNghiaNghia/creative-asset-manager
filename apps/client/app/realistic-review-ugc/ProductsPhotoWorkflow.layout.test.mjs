import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./ProductsPhotoWorkflow.css", import.meta.url), "utf8");
const galleryCss = readFileSync(new URL("./AppliedHatPreview.css", import.meta.url), "utf8");
const twoColumnCss = readFileSync(new URL("./ProductsPhotoWorkflowTwoColumns.css", import.meta.url), "utf8");

describe("Products Stage 2 photo workflow layout", () => {
  it("keeps Run all status and three actions on a single compact desktop toolbar", () => {
    const bar = [...css.matchAll(/\.rrugc-products-photo-pipeline-summary\s*\{([^}]*)\}/g)]
      .map(match => match[1]).find(rule => rule.includes("grid-template-columns:minmax(0,1fr) max-content")) || "";
    expect(bar).toContain("display:grid");
    expect(bar).toContain("grid-template-columns:minmax(0,1fr) max-content");
    expect(bar).toContain("align-items:center");
    expect(css).toContain(".rrugc-products-photo-run-all-copy");
    expect(css).toContain(".rrugc-products-photo-footer-actions");
    expect(css).toContain(".rrugc-products-photo-run-all-report ol");
    expect(css).toContain("position:absolute");
    expect(css).toContain("@media(max-width:950px)");
    expect(css).toContain("@media(max-width:620px)");
  });

  it("uses independent vertical stacks with natural card heights and responsive single-column order", () => {
    expect(twoColumnCss).toMatch(/\.rrugc-products-photo-stage-list\.is-two-column\s*>\s*\.rrugc-products-photo-stage-column\s*\{[^}]*display:flex;[^}]*flex-direction:column;[^}]*align-self:start;/);
    expect(twoColumnCss).toMatch(/\.rrugc-products-photo-stage-list\.is-two-column\s*>\s*\.rrugc-products-photo-stage-column\s*>\s*\.rrugc-products-photo-stage-row\s*\{[^}]*flex:0 0 auto;[^}]*height:auto;/);
    expect(twoColumnCss).not.toMatch(/stage-row:nth-child\(\d+\)\{grid-area:/);
    expect(twoColumnCss).toContain("@container (max-width:1050px)");
    expect(twoColumnCss).toContain("grid-template-columns:minmax(0,1fr)");
  });

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

  it("places Skill 5 and 6 setup info in the first column instead of an empty Ecommerce gallery", () => {
    const panel = css.match(/\.rrugc-products-photo-stage-row:not\(\.is-colorway\) \.rrugc-products-photo-stage-detail\.is-bundled-skill-detail\{([^}]*)\}/)?.[1] ?? "";
    const info = css.match(/\.rrugc-products-photo-stage-row:not\(\.is-colorway\) \.rrugc-products-photo-stage-detail\.is-bundled-skill-detail > \.rrugc-products-photo-detail-info\{([^}]*)\}/)?.[1] ?? "";
    const actions = css.match(/\.rrugc-products-photo-stage-row:not\(\.is-colorway\) \.rrugc-products-photo-stage-detail\.is-bundled-skill-detail > \.rrugc-products-photo-detail-actions\{([^}]*)\}/)?.[1] ?? "";
    expect(panel).toContain("display:grid");
    expect(panel).toContain("grid-template-columns:minmax(0,1fr)");
    expect(info).toContain("grid-column:1");
    expect(actions).toContain("grid-column:2");
    expect(css).toContain("@media(max-width:900px)");
    expect(css).toContain("grid-column:1;grid-row:2;align-items:flex-start");
  });

  it("shows five embroidery gallery tiles while keeping responsive previews", () => {
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