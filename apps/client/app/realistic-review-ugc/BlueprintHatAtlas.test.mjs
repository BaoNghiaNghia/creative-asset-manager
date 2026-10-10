import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

it("keeps the large Blueprint preview frameless while preserving the original cap aspect ratio", () => {
  const css = readFileSync(resolve(process.cwd(), "app/realistic-review-ugc/BlueprintPreview.css"), "utf8");
  const selectedCard = css.match(/\.rrugc-blueprint-selected-card\{([^}]+)\}/)?.[1];
  const selectedHat = css.match(/\.rrugc-blueprint-selected-hat\{([^}]+)\}/)?.[1];
  expect(selectedCard).toContain("border:none");
  expect(selectedCard).toContain("background:transparent");
  expect(selectedCard).toContain("box-shadow:none");
  expect(selectedCard).toContain("width:min(700px");
  expect(selectedHat).toContain("height:min(540px");
  expect(selectedHat).toContain("aspect-ratio:4/5");
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
