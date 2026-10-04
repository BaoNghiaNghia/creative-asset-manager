// @ts-expect-error Vitest executes this test-only import in Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(
  new URL("../../styles/responsive-platform.css", import.meta.url),
  "utf8",
);

describe("Asset Explorer responsive grid", () => {
  it("uses fluid desktop and tablet tracks instead of fixed-width cards", () => {
    expect(styles).toContain("--cam-asset-card-width:136px");
    expect(styles).toContain(
      "repeat(auto-fill,minmax(min(var(--cam-asset-card-width),100%),1fr))",
    );
    expect(styles).not.toContain(
      "repeat(auto-fill,var(--cam-asset-card-width))",
    );
    expect(styles).toContain("justify-content:stretch");
  });

  it("keeps a predictable two-column touch layout on phones", () => {
    expect(styles).toContain(
      ".product-shell .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px",
    );
    expect(styles).toContain(
      ".product-shell .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}",
    );
  });
});
