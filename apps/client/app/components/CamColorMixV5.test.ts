// @ts-expect-error Vitest resolves this Node-only CSS source import.
import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("CAM Color Mix V5 navigation assets", () => {
  it("contains exactly 22 vector navigation icons with the approved three-color palette", () => {
    const folder = new URL("../../assets/navigation/", import.meta.url);
    const icons = (readdirSync(folder) as string[]).filter(name => name.endsWith(".svg"));
    expect(icons).toHaveLength(22);
    for (const name of icons) {
      const svg = readFileSync(new URL(name, folder), "utf8");
      expect(svg, name).toContain('viewBox="0 0 24 24"');
      for (const color of ["#2563EB", "#34D399", "#FFAA45"]) expect(svg, name).toContain(color);
      expect(svg, name).not.toMatch(/<script|<foreignObject|https?:\/\/(?!www\.w3\.org)/i);
    }
  });
  it("loads the local variable font and shared V5 styles", () => {
    const boot = readFileSync(new URL("../main.tsx", import.meta.url), "utf8");
    expect(boot).toContain('import "@fontsource-variable/inter";');
    expect(boot).toContain('import "../styles/cam-color-mix-v5.css";');
  });
});
