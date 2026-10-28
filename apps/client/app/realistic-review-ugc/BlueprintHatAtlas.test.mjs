import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "app/realistic-review-ugc/BlueprintPreview.css"), "utf8");
const preview = readFileSync(resolve(process.cwd(), "app/realistic-review-ugc/BlueprintPreview.tsx"), "utf8");
const detailCss = readFileSync(resolve(process.cwd(), "app/realistic-review-ugc/BlueprintFrontDetailModal.css"), "utf8");

const lastRule = (text, selector) => {
  const withoutComments = text.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [...withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, name]) => name.trim() === selector);
  expect(rules.length).toBeGreaterThan(0);
  return Object.fromEntries(rules.at(-1)[2].split(";").map(part => part.trim())
    .filter(Boolean).map(part => part.split(/:(.*)/s).slice(0, 2).map(value => value.trim())));
};

it("maintains the real cap reference scale and safe Front placement", () => {
  expect(css).toContain("grid-template-columns:repeat(2,minmax(0,1fr))");
  expect(css).toContain(".rrugc-blueprint-panel-header{");
  expect(css).toContain(".rrugc-blueprint-magnifier-scene>.rrugc-blueprint-front-scene");
  expect(lastRule(css, ".rrugc-blueprint-front-scene")).toMatchObject({
    "--rrugc-guide-left": "15.2%",
    "--rrugc-guide-top": "30.9%",
    "--rrugc-guide-width": "69.6%",
    "--rrugc-guide-height": "23.1%",
  });
  const frame = {
    left: "var(--rrugc-guide-left)",
    top: "var(--rrugc-guide-top)",
    width: "var(--rrugc-guide-width)",
    height: "var(--rrugc-guide-height)",
  };
  expect(lastRule(css, ".rrugc-blueprint-selected-design")).toMatchObject(frame);
  expect(lastRule(detailCss, ".rrugc-blueprint-detail-image .rrugc-blueprint-selected-design")).toMatchObject(frame);
});

it("keeps painted guide geometry synchronized between front, lens and detail", () => {
  expect(preview).toContain("frontFit.guideStyle");
  expect(preview).toContain('className="rrugc-blueprint-front-guide is-artwork-fit"');
  expect(detailCss).toContain(".rrugc-blueprint-detail-image .rrugc-blueprint-selected-design");
  expect(css).toContain(".rrugc-blueprint-selected-design>.rrugc-blueprint-front-guide.is-artwork-fit");
  expect(css).not.toContain(".rrugc-blueprint-view-zoom-caption");
});
it("makes the verified Side stock photo authoritative and visible at native orientation", () => {
  const selectors = [
    ".rrugc-blueprint-panel-visual>.rrugc-blueprint-side-image.is-stock",
    ".rrugc-blueprint-side-image.is-stock>.rrugc-blueprint-side-hat",
    ".rrugc-blueprint-side-image.is-stock .rrugc-blueprint-magnifier-scene>.rrugc-blueprint-side-hat",
  ];
  for (const name of selectors) expect(css).toContain(name);
  expect(css).toContain("object-fit:contain;object-position:center;transform:none");
  expect(preview).toContain("blueprintAvailableColors(stockCatalog)");
  expect(preview).toContain("Official 8869 side stock unavailable");
  expect(preview).not.toContain("roberts-reference");
  expect(preview).not.toContain("sides-atlas-fit.webp");
  expect(preview).not.toContain("scaleX(-1)");
  expect(preview).not.toContain("BlueprintSideGuides");
});

it("restores a visible magnifying glass affordance and both hover lenses", () => {
  expect(css).toContain(".rrugc-blueprint-side-image.is-stock .rrugc-blueprint-magnifier{z-index:20}");
  expect(css).toContain(".rrugc-blueprint-selected-hat .rrugc-blueprint-detail-affordance,");
  expect(css).toContain(".rrugc-blueprint-side-image.is-stock .rrugc-blueprint-detail-affordance{");
  expect(preview).toContain("Hover to zoom");
  expect(preview).toContain("frontLens");
  expect(preview).toContain("sideLens");
  expect(preview).toContain("fallbackArtworkStyle");
});

it("does not allow the undersized bundled Natural/Black JPEGs as stock fallbacks", () => {
  expect(preview).not.toContain("/rrugc/blueprint/stock/natural-black");
  expect(preview).toContain("Missing synchronized stock is NOT replaced");
  expect(preview).toContain("frontHatSrc");
  expect(preview).toContain("sideHatSrc");
});