import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./ProductsPhotoWorkflow.css", import.meta.url), "utf8");

describe("Products Stage 2 photo workflow layout", () => {
  it("reserves separate columns for readable titles and shot previews", () => {
    expect(css).toContain('grid-template-areas:"title outputs" "skill controls"');
    expect(css).toMatch(/\.rrugc-products-photo-stage-title strong\{[^}]*white-space:normal/);
    expect(css).toMatch(/\.rrugc-products-photo-output-strip\{[^}]*grid-area:outputs/);
    expect(css).toMatch(/\.rrugc-products-photo-pipeline\{[^}]*min-width:0/);
    expect(css).not.toContain("min-width:790px");
  });

  it("stacks all workflow controls on narrow screens without hiding outputs", () => {
    expect(css).toContain("@media(max-width:700px)");
    expect(css).toContain('grid-template-areas:"title" "outputs" "skill" "controls"');
    expect(css).toContain("overflow-x:auto");
  });
});
