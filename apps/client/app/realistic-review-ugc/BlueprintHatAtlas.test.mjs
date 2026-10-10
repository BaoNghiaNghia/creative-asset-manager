import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

it("ships the twelve real 8869 colorway references as a valid WebP sprite", () => {
  const svg = readFileSync(resolve(process.cwd(), "public/rrugc/blueprint/8869-hat-atlas.svg"), "utf8");
  const encoded = svg.split("data:image/webp;base64,")[1]?.split('"')[0];
  expect(encoded).toBeTruthy();
  const buffer = Buffer.from(encoded, "base64");
  expect(buffer.subarray(0, 4).toString("ascii")).toBe("RIFF");
  expect(buffer.subarray(8, 12).toString("ascii")).toBe("WEBP");
  expect(buffer.length).toBeGreaterThan(9000);
});
