import { describe, expect, it } from "vitest";
import { sharedGarmentColors } from "./productColorMatch";
import type { DesignAsset } from "./api";

const sha = "a".repeat(64);
const make = (position: string, id: string, eligible: string[], unsafe: string[] = []): DesignAsset => ({
  id, job_id: "e".repeat(36), listing_key: "123456789",
  position, variant: 1, method: "imagegen", source: "mapped",
  status: "needs_review", output_sha256: sha, thumbnail_url: "/thumb",
  original_url: "/orig", print_ready: false, rights_status: "unverified",
  manual_qa_required: true, color_match: {
    version: "colormatch-sms-c1717-v10", output_sha256: sha,
    recommended: eligible.slice(0, 10), eligible_colors: eligible,
    unsafe_colors: unsafe, method: "design_region_alpha_sales_prior",
    analysis: {resolution: [512, 512], source: "medium", regions: [],
      text_regions: 0, microtext_regions: 0, faded_ink: false,
      print_review_required: false},
  },
});

describe("shared product garment color selection", () => {
  it("intersects actual safe colors across different positions only", () => {
    const front = make("front", "a".repeat(24), ["white", "ivory", "true-navy"], ["true-navy"]);
    const sleeve = make("right_sleeve", "b".repeat(24), ["ivory", "true-navy", "white"]);
    const result = sharedGarmentColors([front, sleeve], 0);
    expect(result?.positions).toEqual(["front", "right_sleeve"]);
    expect(result?.colors).toEqual(["ivory", "white"]);
    expect(result?.awaitingAnalysis).toBe(false);
  });

  it("swapping the active design changes only its position", () => {
    const a = make("front", "a".repeat(24), ["white", "ivory"]);
    const alt = make("front", "c".repeat(24), ["pepper", "ivory"]);
    const sleeve = make("left_sleeve", "b".repeat(24), ["ivory", "white", "pepper"]);
    expect(sharedGarmentColors([a, alt, sleeve], 0)?.colors).toEqual(["ivory", "white"]);
    expect(sharedGarmentColors([a, alt, sleeve], 1)?.colors).toEqual(["ivory", "pepper"]);
  });

  it("does not invent positions for unknown artwork or mix only variants", () => {
    expect(sharedGarmentColors([
      make("unknown", "a".repeat(24), ["white"]),
      make("unknown", "b".repeat(24), ["ivory"]),
    ], 0)).toBeNull();
    expect(sharedGarmentColors([
      make("front", "a".repeat(24), ["white"]),
      make("front", "b".repeat(24), ["ivory"]),
    ], 0)).toBeNull();
  });

  it("never treats V10 review-only swatches as shared production-ready colors", () => {
    const front = make("front", "a".repeat(24), ["white"]);
    const sleeve = make("right_sleeve", "b".repeat(24), ["white", "ivory"]);
    front.color_match!.recommended = ["white", "ivory"];
    front.color_match!.review_colors = ["ivory"];
    const result = sharedGarmentColors([front, sleeve], 0);
    expect(result?.colors).toEqual(["white"]);
  });

  it("holds the whole-product promise if a position is still on V6", () => {
    const front = make("front", "a".repeat(24), ["white"]);
    const sleeve = make("right_sleeve", "b".repeat(24), ["white"]);
    sleeve.color_match!.version = "colormatch-sms-c1717-v7";
    expect(sharedGarmentColors([front, sleeve], 0)).toMatchObject({
      awaitingAnalysis: true, colors: [],
    });
  });

  it("does not offer colors if both real positions lack shared legible options", () => {
    const result = sharedGarmentColors([
      make("front", "a".repeat(24), ["white"]),
      make("back", "b".repeat(24), ["black"]),
    ], 0);
    expect(result?.colors).toEqual([]);
    expect(result?.awaitingAnalysis).toBe(false);
  });
});
