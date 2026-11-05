import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./ui-overhaul.css", import.meta.url), "utf8");
const table = readFileSync(new URL("./SourcePlanTable.tsx", import.meta.url), "utf8");

describe("Stage 3 Source Image thumbnails (Reviews + TikTokShop)", () => {
  it("makes thumbnails 30% larger in the shared Stage 3 source column only", () => {
    expect(table).toContain('rrugc-source-plan-table rrugc-stage3-source-table');
    const size = css.match(
      /\.rrugc-stage3-source-table \.rrugc-source-group-track \.rrugc-source-thumb\{([^}]*)\}/,
    )?.[1] || "";
    expect(size).toContain("flex:0 0 68px");
    expect(size).toContain("width:68px");
    expect(size).toContain("height:68px");
    expect(68 / 52).toBeGreaterThanOrEqual(1.3);
    // Keep Stage 4's shared thumbnail style unchanged.
    expect(css).toContain(
      ".rrugc-source-group-track .rrugc-source-thumb{flex:0 0 52px;width:52px;height:52px",
    );
  });
});
