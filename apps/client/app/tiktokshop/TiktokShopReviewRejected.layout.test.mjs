import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("./tiktokshop.css", import.meta.url)), "utf8");

describe("TikTokShop-only Stage 3 Review rejected styling", () => {
  it("limits the compact action to TikTokShop Stage 3 and keeps it content-sized", () => {
    const rule = css.match(
      /\.rrugc-tiktokshop-workspace #tiktok-panel-stage3 \.rrugc-source-ref-review-rejected\s*\{([^}]+)\}/,
    )?.[1] || "";
    expect(rule).toContain("width: max-content");
    expect(rule).toContain("height: 29px");
    expect(rule).toContain("font-size: 10px");
    expect(rule).toContain("min-height: 29px");
    expect(rule).toContain("flex: none");
    expect(css).toContain(
      ".rrugc-tiktokshop-workspace #tiktok-panel-stage3 .rrugc-source-ref-empty:has(> .rrugc-source-ref-review-rejected)",
    );
  });

  it("moves the recovery action below the message on narrow screens", () => {
    expect(css).toContain("@media (max-width: 480px)");
    expect(css).toMatch(/\.rrugc-tiktokshop-workspace #tiktok-panel-stage3 \.rrugc-source-ref-review-rejected\s*\{\s*grid-column: 2;/);
  });

  it("does not add an unscoped style affecting Realistic Review UGC", () => {
    expect(css).not.toMatch(/^\.rrugc-source-ref-review-rejected\s*\{/m);
    expect(css).not.toMatch(/^\.rrugc-source-ref-empty\s*\{/m);
  });
});
