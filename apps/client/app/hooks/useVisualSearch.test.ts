import { describe, expect, it } from "vitest";
import { committedVisualQuery, normalizeCrop, visualErrorMessage } from "./useVisualSearch";

describe("visual search client helpers", () => {
  it("clamps normalized crop inside image bounds", () => {
    expect(normalizeCrop({ x: 0.9, y: -1, width: 0.8, height: 2 })).toEqual({
      x: 0.9, y: 0, width: 0.09999999999999998, height: 1,
    });
  });

  it("uses controlled API detail messages", () => {
    expect(visualErrorMessage({ detail: { message: "Visual search is busy." } })).toBe("Visual search is busy.");
  });

  it("snapshots normalized crop, text, and scope for a committed request", () => {
    expect(committedVisualQuery({ x: 0.9, y: 0, width: 0.8, height: 1 }, " outdoor ", "google-drive", "source-a")).toEqual({
      crop: { x: 0.9, y: 0, width: 0.09999999999999998, height: 1 },
      text: "outdoor", provider: "google-drive", externalSourceId: "source-a",
    });
  });
});
