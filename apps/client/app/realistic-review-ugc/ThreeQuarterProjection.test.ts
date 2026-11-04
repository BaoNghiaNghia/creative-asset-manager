import { describe, expect, it } from "vitest";
import { DEFAULT_FRONT_PLACEMENT } from "./FrontPlacement";
import { UV_PROFILE_ID, frontUvBox, projectProfileUv, uvProfileView } from "./ThreeQuarterProjection";

const front = {
  ...DEFAULT_FRONT_PLACEMENT,
  left: .212142, top: .328244, width: .560727, height: .232605,
  upper_line: .270188, lower_line: .597924, revision: 12,
};
const bounds = { x: 39, y: 54, width: 343, height: 118 };

describe("single saved FRONT UV, 5x5 3/4 grid and actual occlusion", () => {
  it("uses the saved front placement for a canonical UV box, centered at u=0.5", () => {
    const box = frontUvBox(bounds, front, 100);
    expect((box[0] + box[2]) / 2).toBe(.5);
    expect((box[1] + box[3]) / 2).toBeCloseTo(
      ((front.top + front.height / 2) * 1254 - 300) / 515, 8,
    );
    expect(box[0]).toBeGreaterThan(0);
    expect(box[3]).toBeLessThan(1);
    expect(UV_PROFILE_ID).toBe("white-navy-1254-front-v2");
  });

  it("uses the SAME UV for front and 3/4 and correctly applies profile pixel dimensions", () => {
    const box = frontUvBox(bounds, front, 100);
    expect(projectProfileUv("front", .5, .5)).toEqual([617.601897, 557.5]);
    expect(projectProfileUv("threeQuarter", .5, .5)).toEqual([455.948967, 521.164319]);
    const [x, y] = projectProfileUv("threeQuarter", .5, .5, 627, 627);
    expect(x).toBeCloseTo(455.948967 / 2, 6);
    expect(y).toBeCloseTo(521.164319 / 2, 6);
    // The target mask is not a placement input; the box never changes.
    expect(box).toEqual(frontUvBox(bounds, front, 100));
    expect(uvProfileView("threeQuarter").visibleMaskPath).toContain("C338,620");
  });

  it("baseline maps along the exact saved grid row without dome warp", () => {
    const grid = uvProfileView("threeQuarter");
    for (let col = 0; col <= 4; col++) {
      const p = projectProfileUv("threeQuarter", col / 4, .5);
      expect(p[0]).toBeCloseTo(grid.nodes[2][col][0], 6);
      expect(p[1]).toBeCloseTo(grid.nodes[2][col][1], 6);
    }
    const start = projectProfileUv("threeQuarter", .25, .5);
    const end = projectProfileUv("threeQuarter", .5, .5);
    const middle = projectProfileUv("threeQuarter", .375, .5);
    expect(middle[0]).toBeCloseTo((start[0] + end[0]) / 2, 6);
    expect(middle[1]).toBeCloseTo((start[1] + end[1]) / 2, 6);
  });

  it("honors saved zoom and intentional off-center FRONT placement without 3/4 refitting", () => {
    const small = frontUvBox(bounds, front, 50);
    const normal = frontUvBox(bounds, front, 100);
    expect(small[2] - small[0]).toBeLessThan(normal[2] - normal[0]);
    expect(small[3] - small[1]).toBeLessThan(normal[3] - normal[1]);
    const shifted = frontUvBox(bounds,
      { ...front, left: .20, width: .32, top: .4, height: .2 }, 100);
    expect((shifted[0] + shifted[2]) / 2).not.toBeCloseTo(.5, 3);
  });

  it("fails closed when saved FRONT exceeds the mesh instead of squeezing into the visible silhouette", () => {
    expect(() => frontUvBox(bounds, { ...front, top: .08 }, 100))
      .toThrow("exceeds the UV mesh");
  });
});
