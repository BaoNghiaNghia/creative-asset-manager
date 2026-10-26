import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./ui-overhaul.css", import.meta.url), "utf8");

describe("Stage 3 References modal header layout", () => {
  it("separates the title and subtitle from right-aligned filter tabs and close button", () => {
    const header = css.match(/\.rrugc-source-review-modal > \.rrugc-reference-review-header\{([^}]*)\}/)?.[1] || "";
    expect(header).toContain("display:grid");
    expect(header).toContain('grid-template-areas:"heading filters close"');
    expect(header).toContain("grid-template-columns:minmax(0,1fr) max-content 34px");

    const filters = css.match(/\.rrugc-source-review-modal \.rrugc-reference-review-header > \.rrugc-source-review-filters\{([^}]*)\}/)?.[1] || "";
    expect(filters).toContain("grid-area:filters");
    expect(filters).toContain("justify-content:flex-end");
    expect(filters).toContain("justify-self:end");
    expect(filters).toContain("margin:0");

    const close = css.match(/\.rrugc-source-review-modal \.rrugc-reference-review-header > \.rrugc-source-review-close\{([^}]*)\}/)?.[1] || "";
    expect(close).toContain("grid-area:close");
    expect(close).toContain("justify-self:end");
  });

  it("provides readable two-line filenames and non-overlapping image counts, including narrow screens", () => {
    const heading = css.match(/\.rrugc-source-review-modal \.rrugc-reference-review-header > \.rrugc-reference-review-heading\{([^}]*)\}/)?.[1] || "";
    expect(heading).toContain("flex-direction:column");
    expect(heading).toContain("gap:4px");
    const title = css.match(/\.rrugc-reference-review-header \.rrugc-reference-review-heading h2\{([^}]*)\}/)?.[1] || "";
    expect(title).toContain("-webkit-line-clamp:2");
    expect(title).toContain("overflow-wrap:anywhere");
    expect(title).toContain("white-space:normal");
    expect(title).toContain("line-height:1.35");
    const subtitle = css.match(/\.rrugc-reference-review-header \.rrugc-reference-review-heading p\{([^}]*)\}/)?.[1] || "";
    expect(subtitle).toContain("line-height:1.5");
    expect(css).toContain("@media(max-width:850px)");
    expect(css).toContain('grid-template-areas:"heading close" "filters filters"');
  });
});
