import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import {
  autoScoutBootstrapCommand,
  autoScoutCommand,
  scoutClientIsCurrent,
  scoutLocalConfig,
} from "./PinterestAutoScoutPanel";
import { RealisticReviewUgcPage } from "./RealisticReviewUgcPage";
import {
  SourcePlanTable,
  sourcePlanPageCount,
  sourcePlanProgressPercent,
} from "./SourcePlanTable";
import type { SourcePlan } from "./types";

function makePlan(referenceCount = 0): SourcePlan {
  return {
    id: "plan-1",
    root_folder_id: "1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX",
    source_file_id: "drive-file-1",
    source_parent_folder_id: "folder-1",
    source_relative_path: "Navy/Hotdog/front.png",
    source_name: "front.png",
    source_mime_type: "image/png",
    source_size_bytes: 1024,
    source_width: 1200,
    source_height: 1200,
    source_modified_at: "2026-10-02T10:00:00Z",
    source_web_url: "https://drive.example/file",
    source_preview_url: "/api/v1/realistic-review-ugc/source-plans/plan-1/image",
    source_revision: "rev-1",
    analysis_revision: 1,
    target_count: 20,
    status: "ready",
    visual_context: {
      themes: ["summer", "funny food"],
      scene_hints: ["backyard cookout"],
      summary: "Playful cookout context",
    },
    campaign_id: "campaign-1",
    campaign_name: "Pinterest refs — Navy/Hotdog/front.png",
    campaign_status: "running",
    scout_status: "ready",
    auto_scout: true,
    search_queries: ["friends backyard cookout wearing caps"],
    progress_count: Math.min(referenceCount, 20),
    pipeline_count: 2,
    candidate_count: referenceCount,
    approved_count: referenceCount,
    drive_ready_count: referenceCount,
    scan_next_at: null,
    scan_last_completed_at: null,
    reference_previews: Array.from({ length: referenceCount }, (_, index) => ({
      id: "ref-" + index,
      pin_url: "https://pinterest.example/pin/" + index,
      image_url: "https://img.example/" + index + ".jpg",
      status: "drive_ready",
      source_query: "cookout cap",
      width: 800,
      height: 1000,
      created_at: "2026-10-02T10:00:00Z",
    })),
    last_error_code: null,
    analyzed_at: "2026-10-02T10:00:00Z",
    created_at: "2026-10-02T10:00:00Z",
    updated_at: "2026-10-02T10:00:00Z",
  };
}

describe("Realistic Review UGC source-first workspace", () => {
  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("renders the source-first UI and removes legacy campaign/candidate/product panels", () => {
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain("Source auto scan");
    expect(markup).toContain("Embroidery source → Pinterest refs");
    expect(markup).toContain("Auto scan on");
    expect(markup).toContain("1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX");
    expect(markup).not.toContain("REFERENCE CAMPAIGNS");
    expect(markup).not.toContain("Product reference library");
    expect(markup).not.toContain("Pinterest candidates");
    expect(markup).not.toContain("ACTIVE CAMPAIGN");
  });

  it("paginates source rows instead of growing an infinite table", () => {
    expect(sourcePlanPageCount(0, 20)).toBe(1);
    expect(sourcePlanPageCount(20, 20)).toBe(1);
    expect(sourcePlanPageCount(21, 20)).toBe(2);
    expect(sourcePlanPageCount(101, 50)).toBe(3);
  });

  it("renders more than twenty references in one horizontal slider", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan(25)]}
        total={1}
        page={1}
        pageSize={20}
        query=""
        syncing={false}
        message=""
        onSync={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
        onQueryChange={() => undefined}
      />,
    );
    expect(markup).toContain("25 refs loaded");
    expect(markup).toContain("rrugc-source-ref-slider");
    expect(markup).toContain("rrugc-source-ref-track");
    expect(markup).toContain("Page 1 / 1");
    expect(markup).not.toContain("rrugc-source-ref-grid");
  });

  it("keeps progress capped at 100 percent when refs exceed the target", () => {
    expect(sourcePlanProgressPercent({ progress_count: 30, target_count: 20 })).toBe(100);
  });

  it("detects outdated local Scout clients without blocking current v12", () => {
    expect(scoutClientIsCurrent("rrugc-scout-v11")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v12")).toBe(true);
    expect(scoutClientIsCurrent(null)).toBe(false);
  });

  it("keeps the one-click Scout setup on one persistent profile", () => {
    const profileDir = "D:\\Bot_Tool_Auto_Game\\scan_pinterest\\pinterest-profile";
    const bootstrap = autoScoutBootstrapCommand(profileDir);
    const command = autoScoutCommand(
      "https://creative.example/",
      "agent-1",
      "agent-secret",
      profileDir,
    );
    const config = scoutLocalConfig(
      "https://creative.example/",
      "agent-1",
      "agent-secret",
      profileDir,
    );
    expect(bootstrap).toContain("--bootstrap-login");
    expect(command).toContain("--agent-id \"agent-1\"");
    expect(command).not.toContain("--campaign-id");
    expect(config).toContain("RRUGC_AGENT_ID=agent-1");
  });
});
