import { describe, expect, it } from "vitest";
import type { EcommerceJob, ColorwayJob } from "./api";
import {
  PRODUCT_STAGE2_PHOTO_STAGES, PRODUCT_STAGE2_TOTAL_OUTPUTS, productDesignProgress,
} from "./ProductsStage2Progress";
import { PHOTO_COLOR_KEYS } from "./ProductsPhotoWorkflow";

const color = "natural-charcoal";
const photo = (stage: string, status: EcommerceJob["status"] = "completed",
  output_url: string | null = "/photo.png", color_key = color) =>
  ({ stage, status, output_url, color_key, source_plan_id: "one" }) as EcommerceJob;
const hatColor = (color_key: string, status: ColorwayJob["status"] = "completed",
  output_url: string | null = "/color.png") =>
  ({ color_key, status, output_url, source_plan_id: "one" }) as ColorwayJob;
const fullPhotos = () => PRODUCT_STAGE2_PHOTO_STAGES.map(stage => photo(stage));
const fullColors = () => PHOTO_COLOR_KEYS.map(key => hatColor(key));

describe("Products Stage 2 design completion", () => {
  it("counts ten distinct photo slots and twelve natural hat colors", () => {
    expect(PRODUCT_STAGE2_PHOTO_STAGES).toHaveLength(10);
    expect(PHOTO_COLOR_KEYS).toHaveLength(12);
    expect(PRODUCT_STAGE2_TOTAL_OUTPUTS).toBe(22);
    expect(productDesignProgress(fullPhotos(), fullColors(), color)).toEqual({
      state: "complete", completed: 22, total: 22, running: 0, failed: 0,
    });
  });

  it("never labels partially generated designs complete", () => {
    expect(productDesignProgress(fullPhotos().slice(0, 4), [], color)).toMatchObject({
      state: "partial", completed: 4,
    });
    expect(productDesignProgress(fullPhotos().slice(0, 9), fullColors(), color))
      .toMatchObject({ state: "partial", completed: 21 });
    expect(productDesignProgress(fullPhotos(), fullColors().slice(0, 11), color))
      .toMatchObject({ state: "partial", completed: 21 });
    expect(productDesignProgress([photo("prod_s2_ec1", "completed", null)], [], color))
      .toMatchObject({ state: "not-started", completed: 0 });
  });

  it("shows running, queued, failed, or not-started states accurately", () => {
    expect(productDesignProgress([], [], color).state).toBe("not-started");
    expect(productDesignProgress([photo("prod_s2_ec1", "queued", null)], [], color))
      .toMatchObject({ state: "running", completed: 0, running: 1 });
    expect(productDesignProgress([photo("prod_s2_ec1", "failed", null)], [], color))
      .toMatchObject({ state: "failed", completed: 0, failed: 1 });
    expect(productDesignProgress([photo("prod_s2_ec1", "failed", null),
      photo("prod_s2_ec2", "running", null)], [], color))
      .toMatchObject({ state: "failed", running: 1, failed: 1 });
  });

  it("counts Ecommerce+UGC only for the selected hat color, but all twelve colorways", () => {
    const jobs = fullPhotos().map(job => ({ ...job, color_key: "natural-black" }));
    expect(productDesignProgress(jobs, fullColors(), color))
      .toMatchObject({ state: "partial", completed: 12 });
    expect(productDesignProgress(jobs, fullColors(), "natural-black"))
      .toMatchObject({ state: "complete", completed: 22 });
    // Legacy jobs without a color key belong to natural-black only.
    expect(productDesignProgress([photo("prod_s2_ec1", "completed", "/image.png", "")], [], color).completed).toBe(0);
  });

  it("does not infer Not started when one job API is unavailable or not loaded", () => {
    expect(productDesignProgress(fullPhotos(), fullColors(), color, "loading"))
      .toMatchObject({ state: "checking", completed: 0 });
    expect(productDesignProgress(fullPhotos(), fullColors(), color, "unavailable"))
      .toMatchObject({ state: "unavailable", completed: 0 });
    expect(productDesignProgress([photo("legacy-other-slot")], [hatColor("khaki-maroon")], color))
      .toMatchObject({ state: "not-started", completed: 0 });
  });
});
