// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { RealisticReviewUgcPage } from "./RealisticReviewUgcPage";

const INITIAL = window.location.pathname + window.location.search + window.location.hash;
afterEach(() => window.history.replaceState(null, "", INITIAL));

describe("evondevKit UI scope across independent GatorHats workflows", () => {
  it("applies only Reviews styling to all five Reviews stages without removing navigation", () => {
    for (const stage of ["reviews-stage1", "reviews-stage2", "stage3", "stage4", "stage5"]) {
      window.history.replaceState(null, "", "/realistic-review-ugc?workflow=reviews&stage=" + stage);
      const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
      expect(markup).toContain("rrugc-evo-reviews");
      expect(markup).not.toContain("rrugc-evo-products");
      expect(markup).toContain('aria-label="GatorHats Reviews stages"');
      expect(markup).toContain('id="rrugc-tab-reviews-stage1"');
      expect(markup).toContain('id="rrugc-tab-reviews-stage2"');
      expect(markup).toContain('id="rrugc-tab-stage3"');
      expect(markup).toContain('id="rrugc-tab-stage4"');
      expect(markup).toContain('id="rrugc-tab-stage5"');
      expect(markup).not.toContain('id="rrugc-tab-stage0"');
      expect(markup.match(/role="tab" aria-selected=/g)).toHaveLength(5);
      expect(markup).toContain('id="rrugc-panel-' + stage + '"');
    }
  });

  it("switching to Products removes Reviews CSS scope without touching its four-stage rail", () => {
    window.history.replaceState(null, "", "/realistic-review-ugc?workflow=products&stage=stage0");
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain("rrugc-evo-products");
    expect(markup).not.toContain("rrugc-evo-reviews");
    expect(markup).toContain('aria-label="GatorHats Products stages"');
    expect(markup).toContain('id="rrugc-tab-products-stage3"');
    expect(markup.match(/role="tab" aria-selected=/g)).toHaveLength(4);
  });
});
