import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import {
  autoScoutBootstrapCommand,
  autoScoutCommand,
  scoutClientIsCurrent,
  scoutLocalConfig,
} from "./PinterestAutoScoutPanel";
import {
  RealisticReviewUgcPage,
  sourcePlanPageRenderFingerprint,
  stage2JobsRenderFingerprint,
} from "./RealisticReviewUgcPage";
import { Stage2JobTable, Stage2OutputReviewModal, Stage2ReferenceReviewModal } from "./Stage2JobTable";
import {
  ReferenceReviewModal,
  SourceImageReviewModal,
  SourcePlanTable,
  sourcePlanPageCount,
  sourceReviewImageUrl,
  sourcePlanProgressPercent,
} from "./SourcePlanTable";
import type { SourcePlan, SourcePlanOverview, Stage2Job } from "./types";

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


function makeStage2Job(
  id: string,
  status: Stage2Job["status"],
  overrides: Partial<Stage2Job> = {},
): Stage2Job {
  return {
    id,
    source_plan_id: "plan-1",
    campaign_id: "campaign-1",
    source_revision: "rev-1",
    skill_name: "gatorhats-8869-image-studio",
    skill_source: "local",
    skill_id: null,
    skill_version: null,
    skill_bundle_sha256: null,
    selected_candidate_ids: ["ref-0"],
    reference_count: 1,
    status,
    can_cancel: false,
    cancel_available_until: null,
    processing_job_id: status === "queued" ? null : "processing-" + id,
    provider_request_id: null,
    output_content_type: status === "completed" ? "image/png" : null,
    output_size_bytes: status === "completed" ? 1024 : null,
    output_width: status === "completed" ? 1024 : null,
    output_height: status === "completed" ? 1024 : null,
    output_remote_file_id: status === "completed" ? "remote-" + id : null,
    output_web_url: null,
    last_error_code: status === "failed" ? "IMAGE_GENERATION_FAILED" : null,
    last_error_message: status === "failed" ? "Generation failed." : null,
    queued_at: "2026-10-04T01:00:00Z",
    started_at: status === "queued" ? null : "2026-10-04T01:01:00Z",
    completed_at: status === "completed" || status === "failed" ? "2026-10-04T01:02:00Z" : null,
    created_at: "2026-10-04T01:00:00Z",
    updated_at: "2026-10-04T01:02:00Z",
    ...overrides,
  };
}

const GLOBAL_OVERVIEW: SourcePlanOverview = {
  embroidery_groups: 79,
  source_images: 133,
  working_groups: 61,
  refs_loaded: 420,
  stage2_groups: 57,
  stage2_source_images: 99,
  stage2_drive_ready_refs: 302,
  stage2_active_jobs: 7,
};

describe("Realistic Review UGC source-first workspace", () => {
  it("windows large Stage 1 reference rows instead of mounting every card", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan(50)]}
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
    const cards = markup.match(/class="rrugc-source-ref-card/g) || [];
    expect(cards.length).toBeLessThanOrEqual(8);
    expect(markup).toContain("50 reference images for front.png");
  });

  it("ignores scheduler-only timestamps when deciding whether Stage 1 changed", () => {
    const base = {
      items: [makePlan(12)],
      page: 1,
      page_size: 20,
      total: 1,
      overview: GLOBAL_OVERVIEW,
    };
    const schedulerOnly = {
      ...base,
      items: [{
        ...base.items[0],
        updated_at: "2026-10-05T05:00:00Z",
        scan_next_at: "2026-10-05T05:01:00Z",
        scan_last_completed_at: "2026-10-05T04:59:00Z",
      }],
    };
    const realChange = {
      ...schedulerOnly,
      items: [{
        ...schedulerOnly.items[0],
        progress_count: schedulerOnly.items[0].progress_count + 1,
      }],
    };

    expect(sourcePlanPageRenderFingerprint(schedulerOnly))
      .toBe(sourcePlanPageRenderFingerprint(base));
    expect(sourcePlanPageRenderFingerprint(realChange))
      .not.toBe(sourcePlanPageRenderFingerprint(base));
  });

  it("windows large Stage 2 reference pickers instead of mounting all 40 refs", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan(50)]}
        overview={GLOBAL_OVERVIEW}
        jobs={[]}
        creatingPlanIds={new Set()}
        loading={false}
        total={1}
        page={1}
        pageSize={10}
        message=""
        onCreateJob={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
      />,
    );
    const cards = markup.match(/class="rrugc-stage2-ref /g) || [];
    expect(cards.length).toBeLessThanOrEqual(14);
    expect(markup).toContain("40 Drive-ready references for front.png");
  });

  it("ignores Stage 2 job heartbeat timestamps but keeps real job changes", () => {
    const base = [makeStage2Job("job-fingerprint", "running")];
    const heartbeatOnly = [{
      ...base[0],
      updated_at: "2026-10-05T05:30:00Z",
    }];
    const statusChange = [{
      ...heartbeatOnly[0],
      status: "completed" as const,
      completed_at: "2026-10-05T05:31:00Z",
      output_remote_file_id: "remote-completed",
    }];

    expect(stage2JobsRenderFingerprint(heartbeatOnly))
      .toBe(stage2JobsRenderFingerprint(base));
    expect(stage2JobsRenderFingerprint(statusChange))
      .not.toBe(stage2JobsRenderFingerprint(base));
  });

  it("keeps non-ready embroidery groups visible in Stage 2 so group totals match Stage 1", () => {
    const pendingPlan = {
      ...makePlan(0),
      id: "plan-pending",
      source_name: "pending-group.png",
      status: "pending" as const,
      campaign_id: null,
      embroidery_signature: null,
      visual_context: null,
    };
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[pendingPlan]}
        overview={GLOBAL_OVERVIEW}
        jobs={[]}
        creatingPlanIds={new Set()}
        loading={false}
        total={GLOBAL_OVERVIEW.embroidery_groups}
        page={1}
        pageSize={10}
        message=""
        onCreateJob={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
      />,
    );
    expect(markup).toContain("pending-group.png");
    expect(markup).toContain(">" + GLOBAL_OVERVIEW.embroidery_groups + "<");
    expect(markup).toContain(">" + GLOBAL_OVERVIEW.source_images + "<");
  });

  it("renders Stage 2 as a max-10 Pinterest ref skill job table", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan(12)]}
        overview={GLOBAL_OVERVIEW}
        jobs={[]}
        creatingPlanIds={new Set()}
        onCreateJob={() => undefined}
      />,
    );
    expect(markup).toContain("EMBROIDERY GROUP");
    expect(markup).toContain("Embroidery groups → image generation");
    expect(markup).toContain("Max 10 outputs / batch");
    expect(markup).toContain("Run status · latest 10");
    expect(markup).toContain(">Output<");
    expect(markup).toContain("Skill &amp; generate");
    expect(markup).toContain("Skill ready");
    expect(markup).toContain("0/10 runs");
    expect(markup).toContain("10 not run");
    expect(markup).toContain("$gatorhats-8869-image-studio");
    expect(markup).toContain("11 refs available");
    expect(markup).toContain('aria-label="3 source images with the same embroidery"');
    expect(markup).toContain("front-black.png");
    expect(markup).toContain("front-red.png");
    expect(markup).toContain("same embroidery");
    expect(markup).toContain("Source images");
    expect(markup).toContain(">79<");
    expect(markup).toContain(">133<");
    expect(markup).toContain(">302<");
    expect(markup).toContain(">7<");
    expect(markup).toContain("Generate selected");
    expect(markup).toContain("Manage skills");
    expect(markup).toContain("Page 1 / 1");
    expect(markup).toContain('aria-label="Stage 2 rows per page"');
    expect(markup).toContain("1–1 of 1");
  });

  it("locks Pinterest references that already produced a completed Stage 2 output", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan(12)]}
        overview={GLOBAL_OVERVIEW}
        jobs={[makeStage2Job("job-generated-ref", "completed", {
          source_plan_id: "plan-2",
          selected_candidate_ids: ["ref-0"],
        })]}
        creatingPlanIds={new Set()}
        onCreateJob={() => undefined}
      />,
    );
    expect(markup).toContain("10 refs available · 1 generated");
    expect(markup).toContain("is-generated");
    expect(markup).toContain('title="Already generated"');
    expect(markup).not.toContain("1/10 selected");
  });

  it("shows a 10-second cancel action before queued Stage 2 work is allowed to start", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan(4)]}
        overview={GLOBAL_OVERVIEW}
        jobs={[makeStage2Job("job-cancellable", "queued", {
          can_cancel: true,
          cancel_available_until: new Date(Date.now() + 9000).toISOString(),
        })]}
        creatingPlanIds={new Set()}
        onCreateJob={() => undefined}
        onCancelJobs={() => undefined}
      />,
    );
    expect(markup).toContain("Cancel · ");
    expect(markup).toContain("Generation starts automatically when the 10-second cancel window ends.");
    expect(markup).not.toContain(">Generate selected<");
  });

  it("shows completed, active, failed, and not-run generation slots separately", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan(4)]}
        overview={GLOBAL_OVERVIEW}
        jobs={[
          makeStage2Job("job-done", "completed", { created_at: "2026-10-04T01:04:00Z" }),
          makeStage2Job("job-running", "running", { created_at: "2026-10-04T01:03:00Z" }),
          makeStage2Job("job-failed", "failed"),
        ]}
        creatingPlanIds={new Set()}
        onCreateJob={() => undefined}
      />,
    );
    expect(markup).toContain("3/10 runs");
    expect(markup).toContain("1 done");
    expect(markup).toContain("1 failed");
    expect(markup).toContain("1 active");
    expect(markup).toContain("7 not run");
    expect(markup).toContain("IMAGE_GENERATION_FAILED");
    expect(markup).toContain("Generating");
    expect(markup).toContain("Generated output 1");
    expect(markup).toContain("1 output");
    expect(markup).toContain('class="rrugc-stage2-results"');
    expect(markup).toContain("rrugc-stage2-result-open");
    expect(markup).toContain("Preview generated output");
    expect(markup).not.toContain('class="rrugc-stage2-result" href=');
    expect(markup).toContain(">Not run<");
  });

  it("renders Stage 2 references in a review-style modal with pick controls", () => {
    const plan = makePlan(3);
    const markup = renderToStaticMarkup(
      <Stage2ReferenceReviewModal
        planId={plan.id}
        planName={plan.source_name}
        references={plan.reference_previews}
        selected={[plan.reference_previews[0].id]}
        generated={new Set([plan.reference_previews[1].id])}
        busy={false}
        onToggle={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("STAGE 2 REFERENCE PREVIEW");
    expect(markup).toContain("3 images · 1/10 selected");
    expect(markup).toContain("rrugc-stage2-reference-review-card");
    expect(markup).toContain("rrugc-stage2-review-toggle");
    expect(markup).toContain("Selected");
    expect(markup).toContain("Already generated");
    expect(markup).toContain("Pinterest ↗");
    expect(markup).not.toContain("rrugc-source-review-votes");
  });

  it("renders generated outputs in a review-style modal without feedback tags", () => {
    const plan = makePlan(3);
    const jobs = [
      makeStage2Job("job-output-1", "completed", {
        output_web_url: "https://drive.example/output-1",
      }),
      makeStage2Job("job-output-2", "completed"),
    ];
    const markup = renderToStaticMarkup(
      <Stage2OutputReviewModal
        plan={plan}
        jobs={jobs}
        onClose={() => undefined}
      />,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("GENERATED OUTPUT PREVIEW");
    expect(markup).toContain("2 generated outputs");
    expect(markup).toContain("rrugc-source-review-masonry");
    expect(markup).toContain("rrugc-stage2-output-review-card");
    expect(markup).toContain("Drive ↗");
    expect(markup).not.toContain("rrugc-source-review-votes");
    expect(markup).not.toContain("rrugc-source-review-vote is-good");
    expect(markup).not.toContain("rrugc-source-review-vote is-bad");
    expect(markup).not.toContain("rrugc-source-review-vote is-ai");
    expect(markup).not.toContain(">AI<");
  });

  it("uses server overview totals instead of current-page rows for Stage 1 KPIs", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan(3)]}
        total={79}
        overview={GLOBAL_OVERVIEW}
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
    expect(markup).toContain("Embroidery groups");
    expect(markup).toContain("Source images");
    expect(markup).toContain("Working groups");
    expect(markup).toContain("Refs loaded");
    expect(markup).toContain(">79<");
    expect(markup).toContain(">133<");
    expect(markup).toContain(">61<");
    expect(markup).toContain(">420<");
    expect(markup).not.toContain("This page");
    expect(markup).not.toContain("Working here");
  });

  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("renders Stage 1, Stage 2, and Settings as accessible AI Operations-style tabs", () => {
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('aria-label="Realistic Review UGC sections"');
    expect(markup).toContain('id="rrugc-tab-stage1"');
    expect(markup).toContain('id="rrugc-tab-stage2"');
    expect(markup).toContain('id="rrugc-tab-settings"');
    expect(markup).toContain("Pinterest References");
    expect(markup).toContain("Image Generation");
    expect(markup).toContain("Settings");
    expect(markup).toContain("Auto Scout");
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain('id="rrugc-panel-stage1"');
    expect(markup).toContain('id="rrugc-panel-stage2"');
    expect(markup).toContain('id="rrugc-panel-settings"');
    expect(markup.indexOf('id="rrugc-tab-settings"')).toBeGreaterThan(markup.indexOf('id="rrugc-tab-stage2"'));

    const stage1Start = markup.indexOf('id="rrugc-panel-stage1"');
    const stage2Start = markup.indexOf('id="rrugc-panel-stage2"');
    const settingsStart = markup.indexOf('id="rrugc-panel-settings"');
    expect(markup.slice(stage1Start, stage2Start)).not.toContain('aria-label="Pinterest Auto Scout"');
    expect(markup.indexOf('aria-label="Pinterest Auto Scout"')).toBeGreaterThan(settingsStart);
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


  it("prioritizes the visible source thumbnail while keeping grouped extras lazy", () => {
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
    expect(markup).toContain('src="/api/v1/realistic-review-ugc/source-plans/plan-1/image"');
    expect(markup).toContain('loading="eager"');
    expect(markup).toContain('fetchpriority="high"');
    expect(markup).toContain('loading="lazy"');
    expect(markup).toContain('width="128"');
    expect(markup).toContain('height="128"');
    expect(markup).toContain('decoding="async"');
    expect(markup).not.toContain("rrugc-deferred-img");
  });

  it("renders table skeleton rows while a Stage 1 page is loading", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan()]}
        total={79}
        page={2}
        pageSize={20}
        query=""
        syncing={false}
        loading
        message=""
        onSync={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
        onQueryChange={() => undefined}
      />,
    );
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("rrugc-table-skeleton-row");
    expect(markup).not.toContain("Playful cookout context");
  });

  it("renders table skeleton rows while a Stage 2 page is loading", () => {
    const markup = renderToStaticMarkup(
      <Stage2JobTable
        plans={[makePlan()]}
        jobs={[]}
        total={57}
        page={2}
        pageSize={10}
        creatingPlanIds={new Set()}
        loading
        onCreateJob={() => undefined}
      />,
    );
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("rrugc-table-skeleton-results");
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
    expect(markup).toContain("<b>25</b> refs");
    expect(markup).toContain("<b>0</b> pending AI");
    expect(markup).toContain("<b>1</b> ✓");
    expect(markup).toContain("<b>1</b> ×");
    expect(markup).toContain("rrugc-source-ref-slider");
    expect(markup).toContain("rrugc-source-ref-track");
    expect(markup).toContain("is-picked");
    expect(markup).toContain("is-rejected");
    expect(markup).toContain("✓ Good");
    expect(markup).toContain("× Reject");
    expect(markup).toContain("AI Synthetic");
    expect(markup).toContain("Drive-ready");
    expect(markup).toContain("rrugc-source-ref-vote is-ai");
    expect(markup).toContain("Same embroidery · 3 images");
    expect(markup).toContain("rrugc-source-group-track");
    expect(markup).toContain("3 source images · same embroidery");
    expect(markup).toContain("Page 1 / 1");
    expect(markup).not.toContain("rrugc-source-ref-grid");
  });

  it("renders source thumbnails as modal preview buttons", () => {
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
    expect(markup).toContain("rrugc-source-thumb-open");
    expect(markup).toContain("Preview source image");
    expect(markup).not.toContain('target="_blank" rel="noreferrer" class="rrugc-source-thumb"');
  });

  it("renders source image review modal without reference feedback tags", () => {
    const plan = makePlan();
    const sources = plan.source_group_images!;
    const markup = renderToStaticMarkup(
      <SourceImageReviewModal
        plan={plan}
        sources={sources}
        onClose={() => undefined}
      />,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("SOURCE IMAGE PREVIEW");
    expect(markup).toContain("rrugc-source-image-review-modal");
    expect(markup).toContain("width:914px");
    expect(markup).toContain("rrugc-source-review-masonry");
    expect(markup).toContain("rrugc-source-image-review-card");
    expect(sourceReviewImageUrl(sources[0])).toContain("thumbnail=true");
    expect(sourceReviewImageUrl(sources[0])).toContain("size=1024");
    expect(markup).toContain("Drive ↗");
    expect(markup).not.toContain("rrugc-source-review-votes");
    expect(markup).not.toContain("rrugc-source-review-vote is-good");
    expect(markup).not.toContain("rrugc-source-review-vote is-bad");
    expect(markup).not.toContain("rrugc-source-review-vote is-ai");
    expect(markup).not.toContain(">AI<");
  });

  it("renders a compact centered empty reference state without carousel arrows", () => {
    const markup = renderToStaticMarkup(
      <SourcePlanTable
        plans={[makePlan(0)]}
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
    expect(markup).toContain("rrugc-source-ref-slider is-empty");
    expect(markup).toContain("Waiting for Pinterest refs");
    expect(markup).toContain("Auto Scout will add qualified references here automatically.");
    expect(markup).not.toContain("Scroll front-black.png references left");
    expect(markup).not.toContain("Scroll front-black.png references right");
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
    expect(markup).toContain("<b>0</b> refs");
    expect(markup).toContain("<b>1</b> pending AI");
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
    expect(scoutClientIsCurrent("rrugc-scout-v20")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v21")).toBe(true);
    expect(scoutClientIsCurrent("rrugc-scout-v22")).toBe(true);
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
