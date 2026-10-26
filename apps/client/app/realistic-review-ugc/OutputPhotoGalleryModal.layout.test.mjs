import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";

const css = readFileSync(new URL("./OutputPhotoGalleryModal.css", import.meta.url), "utf8");

describe("All 22 outputs gallery 75/25 column layout", () => {
  it("assigns exactly 3fr / 1fr to the image preview and thumbnail panel", () => {
    const rule = css.match(/\.rrugc-photo-gallery\[data-gallery-layout="all-outputs"\] \.rrugc-photo-gallery-content\s*\{([^}]*)\}/)?.[1] || "";
    expect(rule).toContain("grid-template-columns:minmax(0,3fr) minmax(0,1fr)");
    expect(css).toContain("grid-template-columns:minmax(0,1fr) 274px");
  });

  it("switches back to a single column on mobile without overriding single-image galleries", () => {
    const mobile = css.slice(css.indexOf("@media(max-width:760px)"));
    const mobileRule = mobile.match(/\.rrugc-photo-gallery\[data-gallery-layout="all-outputs"\] \.rrugc-photo-gallery-content\s*\{([^}]*)\}/)?.[1] || "";
    expect(mobileRule).toContain("grid-template-columns:minmax(0,1fr)");
    expect(css).toContain(".rrugc-photo-gallery.is-single .rrugc-photo-gallery-content{grid-template-columns:minmax(0,1fr)}");
  });
});
