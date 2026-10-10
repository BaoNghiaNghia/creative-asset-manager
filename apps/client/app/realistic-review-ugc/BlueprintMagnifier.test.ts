import { describe, expect, it } from "vitest";
import { blueprintLensGeometry, BLUEPRINT_LENS_SIZE, BLUEPRINT_LENS_ZOOM } from "./BlueprintMagnifier";

describe("Blueprint circular hover magnifier geometry", () => {
  it("magnifies the hovered pixel 2.4× with lens centered when possible", () => {
    const g = blueprintLensGeometry(200, 250, 400, 500);
    expect(g).toEqual({
      diameter: BLUEPRINT_LENS_SIZE,
      left: 118, top: 168,
      translateX: 82 - 200 * BLUEPRINT_LENS_ZOOM,
      translateY: 82 - 250 * BLUEPRINT_LENS_ZOOM,
    });
  });
  it("keeps the lens inside the cap while still sampling the cursor at edges", () => {
    expect(blueprintLensGeometry(0, 0, 400, 500).left).toBe(0);
    expect(blueprintLensGeometry(400, 500, 400, 500).top).toBe(336);
  });
  it("shrinks on small previews without negative placement", () => {
    expect(blueprintLensGeometry(30, 40, 120, 96)).toMatchObject({
      diameter: 96, left: 0, top: 0,
    });
  });
});
