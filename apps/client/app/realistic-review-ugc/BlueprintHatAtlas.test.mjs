import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

it("keeps reference cap aspect ratio and isolates the two Blueprint panels", () => {
  const css = readFileSync(resolve(process.cwd(), "app/realistic-review-ugc/BlueprintPreview.css"), "utf8");
  const selectedCard = css.match(/\.rrugc-blueprint-selected-card\{([^}]+)\}/)?.[1];
  const selectedHat = css.match(/\.rrugc-blueprint-selected-hat\{([^}]+)\}/)?.[1];
  expect(selectedCard).toContain("border:none");
  expect(selectedCard).toContain("background:transparent");
  expect(selectedCard).toContain("box-shadow:none");
  expect(selectedCard).toContain("width:min(700px");
  expect(selectedHat).toContain("height:min(540px");
  expect(selectedHat).toContain("aspect-ratio:4/5");
  expect(css).toContain("grid-template-columns:repeat(2,minmax(0,1fr))");
  expect(css).toContain(".rrugc-blueprint-panel-header{");
  expect(css).toContain(".rrugc-blueprint-panel-footer{");
  expect(css).toContain("transform:none;transform-origin:center center");
  expect(css).toContain(".rrugc-blueprint-side-image{background-size:300% 400%");
  expect(css).toContain(".rrugc-blueprint-panel-visual>.rrugc-blueprint-side-image{width:100%;height:auto;max-height:100%;aspect-ratio:70/43}");
  expect(css).not.toContain(".rrugc-blueprint-side-image{background-size:600% 800%");
  expect(css).toContain(".rrugc-blueprint-panel-visual .rrugc-blueprint-side-guide{left:34%;top:30%;width:33%;height:35%}");
});

it("ships all twelve 1000×1250 original 8869 photographs as full-resolution JPEG files", () => {
  const colors = [
    "black","brown","camo-green","charcoal","forest-green","khaki",
    "maroon","mossy-oak-breakup","navy","realtree-all-purpose","red","royal",
  ];
  for (const color of colors) {
    const raw = readFileSync(resolve(process.cwd(), "public/rrugc/blueprint/fronts/" + color + ".jpg"));
    expect(raw.subarray(0, 3).toString("hex")).toBe("ffd8ff");
    expect(raw.length).toBeGreaterThan(120000);
  }
});

it("bundles the 12 uploaded full-cap side photos as safe binary WebP", () => {
  const webp = readFileSync(resolve(process.cwd(), "public/rrugc/blueprint/sides-atlas-fit.webp"));
  expect(webp.toString("ascii", 0, 4)).toBe("RIFF");
  expect(webp.toString("ascii", 8, 12)).toBe("WEBP");
  expect(webp.length).toBeGreaterThan(100000);
  expect(webp.length).toBeLessThan(400000);
});
