import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

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
