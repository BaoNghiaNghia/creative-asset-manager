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
import { Stage2JobTable } from "./Stage2JobTable";
import {
  ReferenceReviewModal,
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
    embroidery_signature: "signature-hotdog",
    embroidery_group_size: 3,
    source_group_images: [
      {
        id: "plan-1",
        source_name: "front.png",
        source_relative_path: "Navy/Hotdog/front.png",
        source_preview_url: "/api/v1/realistic-review-ugc/source-plans/plan-1/image",
        source_web_url: "https://drive.example/file-1",
        source_width: 1200,
        source_height: 1200,
        source_size_bytes: 1024,
      },
      {
        id: "plan-2",
        source_name: "front-black.png",
        source_relative_path: "Black/Hotdog/front.png",
        source_preview_url: "/api/v1/realistic-review-ugc/source-plans/plan-2/image",
        source_web_url: "https://drive.example/file-2",
        source_width: 1200,
        source_height: 1200,
        source_size_bytes: 1024,
      },
      {
        id: "plan-3",
        source_name: "front-red.png",
        source_relative_path: "Red/Hotdog/front.png",
        source_preview_url: "/api/v1/realistic-review-ugc/source-plans/plan-3/image",
        source_web_url: "https://drive.example/file-3",
        source_width: 1200,
        source_height: 1200,
        source_size_bytes: 1024,
      },
    ],
    target_count: 50,
    status: "ready",
    visual_context: {
      themes: ["summer", "funny food"],
      embroidery_text: ["Bad Day To Be A Hotdog"],
      embroidery_identity: "Bad Day To Be A Hotdog wording with hotdog motif",
      scene_hints: ["backyard cookout"],
      summary: "Playful cookout context",
    },
    campaign_id: "campaign-1",
    campaign_name: "Pinterest refs — Navy/Hotdog/front.png",
    campaign_status: "running",
    scout_status: "ready",
    auto_scout: true,
    search_queries: ["friends backyard cookout wearing caps"],
    progress_count: Math.min(referenceCount, 50),
    pipeline_count: 2,
    candidate_count: referenceCount,
    approved_count: referenceCount,
    pending_ai_count: 0,
    drive_ready_count: referenceCount,
    scan_next_at: null,
    scan_last_completed_at: null,
    reference_previews: Array.from({ length: referenceCount }, (_, index) => ({
      id: "ref-" + index,
      pin_url: "https://pinterest.example/pin/" + index,
      image_url: "https://img.example/" + index + ".jpg",
      status: "drive_ready",
      picked: index === 0,
      rejected: index === 1,
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
  it("renders Stage 2 as a max-10 Pinterest ref skill job table", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan(12)]}
        jobs={[]}
        creatingPlanIds={new Set()}
        onCreateJob={() => undefined}
      />,
    );
    expect(markup).toContain("STAGE 2");
    expect(markup).toContain("Max 10 refs / job");
    expect(markup).toContain("$gatorhats-8869-image-studio");
    expect(markup).toContain("11 Drive-ready refs available");
    expect(markup).toContain("Generate master");
  });

  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("renders Stage 1 and Stage 2 as accessible AI Operations-style tabs", () => {
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('aria-label="Realistic Review UGC stages"');
    expect(markup).toContain('id="rrugc-tab-stage1"');
    expect(markup).toContain('id="rrugc-tab-stage2"');
    expect(markup).toContain("Pinterest References");
    expect(markup).toContain("Image Generation");
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain('id="rrugc-panel-stage1"');
    expect(markup).toContain('id="rrugc-panel-stage2"');
    expect(markup).toContain("hidden");
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

  it("renders server-backed sort controls beside the source search", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan()]}
        total={70}
        page={1}
        pageSize={20}
        query=""
        sortBy="group_size"
        sortDirection="desc"
        syncing={false}
        message=""
        onSync={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
        onQueryChange={() => undefined}
        onSortByChange={() => undefined}
        onSortDirectionChange={() => undefined}
      />,
    );
    expect(markup).toContain("rrugc-source-plan-search");
    expect(markup).toContain("rrugc-source-plan-sort");
    expect(markup).toContain('aria-label="Sort source plans by"');
    expect(markup).toContain("Embroidery group size");
    expect(markup).toContain("Large → small");
    expect(markup).toContain("70 embroidery groups");
  });


  it("shows the hand-holding-hat context for hat source plans", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[{ ...makePlan(), reference_contexts: ["hand_holding_hat"] }]}
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
    expect(markup).toContain("Hand holding hat");
  });


  it("lazy-loads source thumbnails behind a stable skeleton", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan()]}
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
    expect(markup).toContain("rrugc-source-thumb-skeleton");
    expect(markup).toContain('loading="lazy"');
    expect(markup).toContain('decoding="async"');
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
    expect(markup).toContain("25 refs · 0 pending AI · 1 ✓ · 1 ×");
    expect(markup).toContain("rrugc-source-ref-slider");
    expect(markup).toContain("rrugc-source-ref-track");
    expect(markup).toContain("is-picked");
    expect(markup).toContain("is-rejected");
    expect(markup).toContain("Pending AI = waiting analysis");
    expect(markup).toContain("AI synthetic/remove + train AI");
    expect(markup).toContain("rrugc-source-ref-vote is-ai");
    expect(markup).toContain("Same embroidery · 3 images");
    expect(markup).toContain("rrugc-source-group-track");
    expect(markup).toContain("3 source images · same embroidery");
    expect(markup).toContain("Page 1 / 1");
    expect(markup).not.toContain("rrugc-source-ref-grid");
  });

  it("does not label a singleton source row as same embroidery", () => {
    const plan = makePlan();
    plan.embroidery_group_size = 1;
    plan.source_group_images = [plan.source_group_images![0]];
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[plan]}
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
    expect(markup).toContain("1 source image");
    expect(markup).not.toContain("1 source image · same embroidery");
  });

  it("shows Scout-saved references while Gemini analysis is pending", () => {
    const plan = makePlan(1);
    plan.approved_count = 0;
    plan.pending_ai_count = 1;
    plan.drive_ready_count = 0;
    plan.reference_previews[0].status = "analysis_queued";
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[plan]}
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
    expect(markup).toContain("0 refs · 1 pending AI");
    expect(markup).toContain("status-analysis_queued");
    expect(markup).toContain("rrugc-source-ref-open");
    expect(markup).toContain("rrugc-source-ref-vote is-ai");
    expect(markup).not.toContain("rrugc-source-ref-ai-state");
  });


  it("renders all references in a large masonry review modal with direct feedback controls", () => {
    const plan = makePlan(3);
    plan.approved_count = 2;
    plan.pending_ai_count = 1;
    plan.reference_previews[2].status = "analysis_queued";
    const markup = renderToStaticMarkup(
      <ReferenceReviewModal
        plan={plan}
        reviewingReferenceIds={new Set<string>()}
        onSetReferenceFeedback={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("rrugc-source-review-masonry");
    expect(markup).toContain("rrugc-source-review-card");
    expect(markup).toContain("is-pending-ai");
    expect(markup).toContain("Pending analysis");
    expect(markup).toContain("Pinterest ↗");
    expect(markup).toContain(">✓<");
    expect(markup).toContain(">×<");
    expect(markup).toContain("rrugc-source-review-vote is-ai");
    expect(markup).toContain(">AI<");
  });

  it("keeps progress capped at 100 percent when refs exceed the target", () => {
    expect(sourcePlanProgressPercent({ progress_count: 60, target_count: 50 })).toBe(100);
  });

  it("requires the text-aware context-first v14 Scout client", () => {
    expect(scoutClientIsCurrent("rrugc-scout-v12")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v13")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v14")).toBe(true);
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
