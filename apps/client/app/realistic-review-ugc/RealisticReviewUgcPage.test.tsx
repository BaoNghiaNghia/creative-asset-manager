import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { CampaignExportPanel } from "./CampaignGenerationPanel";
import { scoutCommand } from "./RealisticReviewUgcPage";

describe("Realistic Review UGC route", () => {
  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("builds a local scout command without changing the API host", () => {
    const command = scoutCommand("https://creative.example/", "campaign-1", "secret-token");
    expect(command).toContain("--base-url \"https://creative.example\"");
    expect(command).toContain("--campaign-id \"campaign-1\"");
    expect(command).toContain("--token \"secret-token\"");
    expect(command).toContain("--profile-dir");
  });

  it("renders Phase 9 export readiness and catalog counts", () => {
    const markup = renderToStaticMarkup(
      <CampaignExportPanel
        summary={{
          generated: 8,
          review_pending: 2,
          approved: 4,
          rejected: 2,
          export_ready: 3,
          exported: 1,
        }}
        busy={false}
        onExport={() => undefined}
      />,
    );
    expect(markup).toContain("PHASE 9 · EXPORT + CATALOG");
    expect(markup).toContain("Export ready (3)");
    expect(markup).toContain("Cataloged");
    expect(markup).toContain(">1<");
  });

  it("disables batch export when nothing is export ready", () => {
    const markup = renderToStaticMarkup(
      <CampaignExportPanel summary={null} busy={false} onExport={() => undefined} />,
    );
    expect(markup).toContain("disabled");
    expect(markup).toContain("Export ready (0)");
  });
});
