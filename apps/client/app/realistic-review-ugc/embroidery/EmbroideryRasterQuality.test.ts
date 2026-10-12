import { describe, expect, it } from "vitest";
import { chooseEmbroideryRasterSize } from "./EmbroideryRasterQuality";

describe("Blueprint embroidery detail raster quality", () => {
  it("keeps lightweight previews and preserves more native pixels in Front detail", () => {
    expect(chooseEmbroideryRasterSize(2800, 1600, "preview")).toEqual({ width: 1800, height: 1029 });
    expect(chooseEmbroideryRasterSize(2800, 1600, "detail")).toEqual({ width: 2800, height: 1600 });
  });

  it("never invents source pixels when PNG is already small", () => {
    expect(chooseEmbroideryRasterSize(768, 768, "detail")).toEqual({ width: 768, height: 768 });
    expect(chooseEmbroideryRasterSize(768, 768, "preview")).toEqual({ width: 768, height: 768 });
  });

  it("limits memory footprint of oversized originals without changing aspect ratio", () => {
    const large = chooseEmbroideryRasterSize(6000, 5000, "detail");
    expect(large.width).toBeLessThanOrEqual(3200);
    expect(large.width * large.height).toBeLessThanOrEqual(7_000_000);
    expect(large.width / large.height).toBeCloseTo(1.2, 2);
    expect(large.width).toBeGreaterThan(1800);
  });

  it("handles degenerate images without allocating oversized buffers", () => {
    expect(chooseEmbroideryRasterSize(0, 0, "detail")).toEqual({ width: 1, height: 1 });
  });
});
