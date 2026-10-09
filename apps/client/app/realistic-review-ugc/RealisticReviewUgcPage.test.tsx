import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import {
  autoScoutBootstrapCommand,
  autoScoutCommand,
  scoutClientIsCurrent,
  scoutLocalConfig,
} from "./PinterestAutoScoutPanel";
import { KeywordAnalysisTable, KeywordDetailModal } from "./KeywordAnalysisTable";
import { EmbroideryColorwayStage } from "./EmbroideryColorwayStage";
import {
  RealisticReviewUgcPage,
  sourcePlanPageRenderFingerprint,
  stage2JobsRenderFingerprint,
  stage2ReferenceBatches,
} from "./RealisticReviewUgcPage";
import { Stage2JobTable, Stage2OutputReviewModal, Stage2ReferenceReviewModal } from "./Stage2JobTable";
import { Stage3ReviewGroups, Stage3ReviewModal } from "./Stage3ReviewGroups";
import {
  ReferenceReviewModal,
  SourceImageReviewModal,
  SourcePlanTable,
  sourcePlanPageCount,
  sourceReviewImageUrl,
  sourcePlanProgressPercent,
} from "./SourcePlanTable";
import type {
  SourcePlan,
  SourcePlanOverview,
  Stage2Job,
  Stage3ReviewGroupList,
  Stage3ReviewImage,
} from "./types";

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

function makeStage3ReviewImage(
  id: string,
  reviewerName: string,
  reviewText: string,
): Stage3ReviewImage {
  return {
    stage2_job_id: id,
    source_plan_id: "plan-" + id,
    source_name: "BachelorettePartySnapbackHat-" + id + ".png",
    source_relative_path: "Reviews/" + id + ".png",
    output_remote_file_id: "remote-" + id,
    output_width: 1024,
    output_height: 1024,
    output_content_type: "image/png",
    completed_at: "2026-10-06T01:00:00Z",
    preview_url: "https://img.example/" + id + ".jpg",
    original_url: "https://img.example/" + id + "-original.png",
    analysis_id: "analysis-" + id,
    analysis_status: "ready",
    final_score: 0.9,
    mobile_ugc_score: 0.9,
    photorealism_score: 0.88,
    product_visibility_score: 0.92,
    review_fit_score: 0.91,
    person_visible: true,
    hat_visible: true,
    product_visible: true,
    embroidery_visible: true,
    scene_type: "casual",
    framing_type: "medium",
    summary: "Natural lifestyle image.",
    reviewer_name: reviewerName,
    star_rating: 5,
    review_text: reviewText,
    review_generated_at: "2026-10-06T01:01:00Z",
    reject_reasons: [],
    last_error_code: null,
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

  it("virtualizes large Stage 2 reference pickers while keeping every ref selectable", () => {
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
    expect(markup).toContain("49 Drive-ready references for front.png");
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

  it("renders the Stage 2 embroidery-to-13-colors workspace", () => {
    const plan = makePlan();
    plan.source_name = "embroidery_bad-day-hotdog.png";
    plan.source_relative_path = "Designs/embroidery_bad-day-hotdog.png";
    plan.source_group_images = [{
      id: plan.id,
      source_name: plan.source_name,
      source_relative_path: plan.source_relative_path,
      source_preview_url: plan.source_preview_url,
      source_web_url: plan.source_web_url,
      source_width: plan.source_width,
      source_height: plan.source_height,
      source_size_bytes: plan.source_size_bytes,
    }];
    const markup = renderToStaticMarkup(
      <EmbroideryColorwayStage
        data={{
          items: [plan],
          page: 1,
          page_size: 20,
          total: 1,
          overview: GLOBAL_OVERVIEW,
        }}
        loading={false}
        syncing={false}
        query=""
        onSync={() => undefined}
        onQueryChange={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
      />,
    );
    expect(markup).toContain('aria-label="Stage 2 rows per page"');
    for (const size of [20, 50, 100, 500]) {
      expect(markup).toContain(`<option value="${size}"`);
    }
    expect(markup).not.toContain('<option value="10"');
    expect(markup).toContain("EMBROIDERY_ SOURCE");
    expect(markup).toContain("Embroidery design → 13 colorways");
    expect(markup).toContain("embroidery_");
    expect(markup).toContain("bad day hotdog");
    expect(markup).toContain("13-color batch");
    expect((markup.match(/rrugc-colorway-slot is-pending/g) || []).length).toBe(13);
    expect(markup).toContain("Run selected · 0");
    expect(markup).toContain("0 completed · 0 active · 0 failed");
  });

  it("splits unlimited Stage 2 selections into continuous groups of three refs", () => {
    expect(stage2ReferenceBatches(["a", "b", "c", "d", "e", "f", "g"])).toEqual([
      ["a", "b", "c"],
      ["d", "e", "f"],
      ["g"],
    ]);
  });

  it("renders Stage 2 with unlimited selection and three refs per generation run", () => {
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
    expect(markup).toContain("Pinterest references → image generation");
    expect(markup).toContain("3 refs + 1 random hat / run");
    expect(markup).toContain("References · unlimited selection");
    expect(markup).not.toContain("Run status · latest 10");
    expect(markup).toContain("Output · runs &amp; results");
    expect(markup).toContain("rrugc-stage4-output-status-summary");
    expect(markup).toContain("Run history · latest 0");
    expect(markup).not.toContain('class="rrugc-stage2-status"');
    expect(markup).toContain("Skill &amp; generate");
    expect(markup).not.toContain("Skill ready");
    expect(markup).toContain("0/10 latest runs");
    expect(markup).toContain("10 not run");
    expect(markup).toContain("$gatorhats-8869-image-studio");
    expect(markup).toContain('aria-label="References: 0 selected, 11 available, 0 generated"');
    expect(markup).toContain('class="rrugc-stage2-ref-head"');
    expect(markup).toContain('class="is-selected"><strong>0</strong> selected');
    expect(markup).toContain('class="is-available"><strong>11</strong> available');
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
    expect(markup).not.toContain("Manage skills");
    expect(markup).toContain("Page 1 / 1");
    expect(markup).toContain('aria-label="Stage 4 rows per page"');
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
    expect(markup).toContain('aria-label="References: 0 selected, 10 available, 1 generated"');
    expect(markup).toContain('class="is-generated"><strong>1</strong> generated');
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

  it("combines completed, active, failed, and not-run status with output and retry actions", () => {
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
    expect(markup).toContain("3/10 latest runs");
    expect(markup).toContain("1 done");
    expect(markup).toContain("1 failed");
    expect(markup).toContain("1 active");
    expect(markup).toContain("7 not run");
    expect(markup).toContain("Run history · latest 3");
    expect(markup).toContain("View failures &amp; retry");
    expect(markup).toContain('class="rrugc-stage4-run-history');
    expect(markup).toContain("IMAGE_GENERATION_FAILED");
    expect(markup).toContain(">Retry</button>");
    expect(markup).toContain(">Logs</button>");
    expect(markup).toContain("Generating");
    expect(markup).toContain("Generated output 1");
    expect(markup).toContain("1 output");
    expect(markup).toContain('class="rrugc-stage2-results"');
    expect(markup).toContain("rrugc-stage2-result-open");
    expect(markup).toContain("Preview generated output");
    expect(markup).not.toContain('class="rrugc-stage2-result" href=');
    expect(markup).toContain("Run history · latest 3");
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
        onBulkSelection={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("STAGE 3 REFERENCE PREVIEW");
    expect(markup).toContain("3 images · 1 selected");
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
        onRegenerateJob={() => undefined}
      />,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("GENERATED OUTPUT PREVIEW");
    expect(markup).toContain("2 generated outputs");
    expect(markup).toContain("rrugc-source-review-masonry");
    expect(markup).toContain("rrugc-stage2-output-review-card");
    expect(markup).toContain("rrugc-stage4-output-card-actions");
    expect(markup).toContain("Logs for output 1");
    expect(markup).toContain("Versions for output 1");
    expect(markup).toContain("Generate new version of output 1");
    for (const icon of ["logs", "versions", "new-version"]) {
      expect(markup).toContain(`data-action-icon="${icon}"`);
    }
    expect(markup).toContain('title="View logs"');
    expect(markup).toContain('title="View versions"');
    expect(markup).toContain('title="Generate new version"');
    expect(markup).not.toContain(">+ New version</button>");
    expect(markup).toContain("rrugc-stage4-output-modal");
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

  it("renders Stage 0 through Stage 5 as accessible workflow tabs with global management actions", () => {
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain('role="tablist"');
    expect(markup).toContain('aria-label="Realistic Review UGC sections"');
    expect(markup).toContain('id="rrugc-tab-stage0"');
    expect(markup).toContain('id="rrugc-tab-stage1"');
    expect(markup).toContain('id="rrugc-tab-stage2"');
    expect(markup).toContain('id="rrugc-tab-stage3"');
    expect(markup).toContain('id="rrugc-tab-stage4"');
    expect(markup).toContain('id="rrugc-tab-stage5"');
    expect(markup).not.toContain('id="rrugc-tab-settings"');
    expect(markup).toContain("Analysis Keyword");
    expect(markup).toContain("Embroidery → 13 Colors");
    expect(markup).toContain("Pinterest References");
    expect(markup).toContain("Image Generation");
    expect(markup).toContain("UGC Review");
    expect(markup).toContain("Manage skills");
    expect(markup).toContain("Settings");
    expect(markup).toContain('aria-selected="true"');
    expect(markup).toContain('id="rrugc-panel-stage0"');
    expect(markup).toContain('id="rrugc-panel-stage1"');
    expect(markup).toContain('id="rrugc-panel-stage2"');
    expect(markup).toContain('id="rrugc-panel-stage3"');
    expect(markup).toContain('id="rrugc-panel-stage4"');
    expect(markup).toContain('id="rrugc-panel-stage5"');
    expect(markup).not.toContain('id="rrugc-panel-settings"');
    expect(markup.indexOf('id="rrugc-tab-stage0"')).toBeLessThan(markup.indexOf('id="rrugc-tab-stage1"'));

    const stage1Start = markup.indexOf('id="rrugc-panel-stage1"');
    const stage2Start = markup.indexOf('id="rrugc-panel-stage2"');
    expect(markup.slice(stage1Start, stage2Start)).toContain("Generate images from used keywords");
    const stage3Start = markup.indexOf('id="rrugc-panel-stage3"');
    expect(markup.slice(stage2Start, stage3Start)).toContain("Embroidery design → 13 colorways");
    expect(markup).not.toContain('aria-label="Pinterest Auto Scout"');
    expect(markup).toContain("hidden");
  });

  it("renders independent Stage 0 keyword-volume rows from the quote-scout terminal", () => {
    const markup = renderToStaticMarkup(
      <KeywordAnalysisTable
        data={{
          items: [
            {
              id: "kv-1",
              keyword: "Bad Day To Be A Hotdog hat",
              search_volume: 4400,
              competition: "HIGH",
              cpc_low: 0.56,
              cpc_high: 1.96,
              trademark_status: "unverified",
              trademark_checked_at: null,
              competition_index: 100,
              three_month_change_pct: -33.1,
              yoy_change_pct: -45.3,
              trend: [
                { period: "2026-08", volume: 2900 },
                { period: "2026-09", volume: 3600 },
                { period: "2026-10", volume: 4400 },
              ],
              source_image_url: "https://i.pinimg.com/736x/aa/bb/hotdog.jpg",
              source_pin_url: "https://www.pinterest.com/pin/123456789/",
              scout_keyword_feedback: "suggested",
              scout_pin_feedback: "suggested",
              picked: true,
              picked_at: "2026-10-05T11:00:00Z",
              favorite: true,
              favorite_at: "2026-10-05T11:00:00Z",
              provider: "aebrowse_google_ads",
              fetched_at: "2026-10-05T10:00:00Z",
              created_at: "2026-10-01T10:00:00Z",
            },
            {
              id: "kv-2",
              keyword: "funny hotdog cap",
              search_volume: 260,
              competition: "MEDIUM",
              cpc_low: 0.31,
              cpc_high: 0.88,
              trend: [
                { period: "2026-08", volume: 210 },
                { period: "2026-09", volume: 230 },
                { period: "2026-10", volume: 260 },
              ],
              source_image_url: null,
              source_pin_url: null,
              scout_keyword_feedback: "blocked",
              picked: false,
              picked_at: null,
              favorite: false,
              favorite_at: null,
              provider: "aebrowse_google_ads",
              fetched_at: "2026-10-05T10:00:00Z",
              created_at: "2026-09-30T10:00:00Z",
            },
          ],
          page: 1,
          page_size: 20,
          total: 2,
          overview: {
            total_keywords: 2,
            total_search_volume: 4660,
            average_search_volume: 2330,
            average_cpc: 0.9275,
            high_competition: 1,
            zero_volume: 0,
            short_tail_keywords: 0,
            mid_tail_keywords: 1,
            long_tail_keywords: 1,
            picked_keywords: 1,
            favorite_keywords: 1,
            suggested_keywords: 1,
          },
        }}
        query=""
        sortBy="search_volume"
        sortDirection="desc"
        usageFilter="all"
        tailFilter="all"
        favoritesOnly={false}
        suggestedOnly={false}
        pickingIds={new Set()}
        favoritingIds={new Set()}
        feedbackUpdatingIds={new Set()}
        onFeedbackChange={() => undefined}
        onSortChange={() => undefined}
        onUsageFilterChange={() => undefined}
        onTailFilterChange={() => undefined}
        onFavoritesOnlyChange={() => undefined}
        onSuggestedOnlyChange={() => undefined}
        onResetAll={() => undefined}
        onOpenSearchIntelligence={() => undefined}
        onPickChange={() => undefined}
        onFavoriteChange={() => undefined}
        onPageChange={() => undefined}
        onPageSizeChange={() => undefined}
        onQueryChange={() => undefined}
      />,
    );

    expect(markup).toContain("<h2>Analysis Keyword</h2>");
    expect(markup).toContain("Live keyword data");
    expect(markup).toContain('aria-label="Open Search Intelligence"');
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup.indexOf("Search Intelligence")).toBeLessThan(markup.indexOf("Live keyword data"));
    expect(markup).toContain("Bad Day To Be A Hotdog hat");
    expect(markup).toContain("Đề xuất");
    expect(markup).toContain("Bỏ đề xuất");
    expect(markup).toContain("Đã đề xuất");
    expect(markup).toContain("Đã bỏ");
    expect(markup.match(/>Đã đề xuất</g)).toHaveLength(2);
    expect(markup).toContain('rrugc-scout-feedback-state is-suggested');
    expect(markup).toContain('rrugc-scout-feedback-state is-blocked');
    expect(markup).toContain('rrugc-scout-feedback-control');
    expect(markup).not.toContain('aria-label="Feedback target:');
    expect(markup).not.toContain('<option value="both"');
    expect(markup).not.toContain('<option value="pin"');
    for (const icon of ["thumbs-up", "thumbs-down", "undo", "heart", "circle-plus", "circle-check"]) {
      expect(markup).toContain(`data-icon="${icon}"`);
    }
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('aria-label="Scout feedback: Bad Day To Be A Hotdog hat"');
    expect(markup).toContain('aria-label="Scout feedback: funny hotdog cap"');
    expect(markup).toContain(">Preview<");
    expect(markup).toContain(">Trend<");
    expect(markup).toContain("https://i.pinimg.com/736x/aa/bb/hotdog.jpg");
    expect(markup).toContain("https://www.pinterest.com/pin/123456789/");
    expect(markup).toContain(">4,400<");
    expect(markup).toContain("HIGH");
    expect(markup).toContain("$0.56");
    expect(markup).toContain("$1.96");
    expect(markup).toContain("Avg searches / mo");
    expect(markup).toContain("3-mo change");
    expect(markup).toContain("YoY change");
    expect(markup).toContain('aria-label="Sort by 3-mo change ascending"');
    expect(markup).toContain('aria-label="Sort by YoY change ascending"');
    expect(markup).toContain("Low CPC ($)");
    expect(markup).toContain("High CPC ($)");
    expect(markup).toContain("Trademark (TM)");
    expect(markup).toContain("Chưa xác minh");
    expect(markup).toContain('aria-label="Sort by Trademark (TM) ascending"');
    expect(markup).toContain('aria-label="Stage 0 rows per page"');
    for (const count of [20, 50, 100, 500]) expect(markup).toContain(`<option value="${count}"`);
    expect(markup).not.toContain('<option value="10"');
    expect(markup).toContain("Total keywords");
    expect(markup).toContain("Keywords / Pins suggested");
    expect(markup).toContain("rrugc-stage0-suggested-kpi");
    expect(markup).toContain("rrugc-stage0-favorite-kpi");
    expect(markup).toContain('aria-label="Keyword overview filters"');
    expect(markup).toContain("rrugc-stage0-total-kpi active");
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain("Short-tail");
    expect(markup).toContain("Mid-tail");
    expect(markup).toContain("Long-tail");
    expect(markup).toContain("2-word keywords");
    expect(markup).toContain("3–4 word keywords");
    expect(markup).toContain("5+ word keywords");
    expect(markup).not.toContain("Average CPC");
    expect(markup).toContain("Google Ads · AEBrowse");
    expect(markup).not.toContain("All usage");
    expect(markup).not.toContain('aria-label="Keyword usage filter"');
    expect(markup).not.toContain(">Unused<");
    expect(markup).toContain("Short-tail");
    expect(markup).toContain("Mid-tail");
    expect(markup).toContain("Long-tail");
    expect(markup).toContain('title="Clear all Stage 0 filters and show every keyword"');
    expect(markup).toContain('role="combobox"');
    expect(markup).toContain('aria-autocomplete="list"');
    expect(markup).not.toContain("Any volume");
    expect(markup).not.toContain('aria-label="Filter keyword volume"');
    expect(markup).toContain('aria-label="Remove favorite: Bad Day To Be A Hotdog hat"');
    expect(markup).toContain('aria-label="Add favorite: funny hotdog cap"');
    expect(markup).toContain('aria-label="Mark unused: Bad Day To Be A Hotdog hat"');
    expect(markup).toContain('aria-label="Pick keyword: funny hotdog cap"');
    expect(markup).toContain('data-pick-state="picked"');
    expect(markup).toContain('data-pick-state="unpicked"');
    expect(markup).toContain('title="Đã Pick · Bấm để bỏ chọn"');
    expect(markup).toContain('title="Chưa Pick · Bấm để chọn"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain('aria-pressed="false"');
    expect(markup).toContain('aria-sort="descending"');
    expect(markup).toContain('aria-label="Sort by Keyword ascending"');
    expect(markup).toContain('aria-label="Sort by Avg searches / mo ascending"');
    expect(markup).toContain('aria-label="Sort by Competition ascending"');
    expect(markup).toContain('aria-label="Sort by Low CPC ($) ascending"');
    expect(markup).toContain('aria-label="Sort by High CPC ($) ascending"');
    expect(markup).toContain('aria-label="Sort by Created date ascending"');
    expect(markup).toContain("Created date");
    expect(markup).toContain('title="Open keyword details"');
    expect(markup).toContain('aria-label="Open details and 12-month Google Ads trend for Bad Day To Be A Hotdog hat"');
    expect(markup).toContain('role="tooltip"');
    expect(markup).toContain("Google Ads monthly search volume");
    expect(markup).toContain("3 mo");
    expect(markup).toContain("-33.1%");
    expect(markup).toContain("YoY");
    expect(markup).toContain("-45.3%");
    expect(markup).toContain("2026-08");
    expect(markup).toContain("2026-10");
  });

  it("renders the complete Stage 0 keyword detail modal with monthly history and provider metadata", () => {
    const markup = renderToStaticMarkup(
      <KeywordDetailModal
        item={{
          id: "kv-detail",
          keyword: "ATLANTA BRAVES",
          search_volume: 22200,
          competition: "HIGH",
          cpc_low: 0.74,
          cpc_high: 2.18,
          trademark_status: "unverified",
          trademark_checked_at: null,
          competition_index: 92,
          three_month_change_pct: -18.1,
          yoy_change_pct: 83.1,
          trend: [
            { period: "2026-07", volume: 18100 },
            { period: "2026-08", volume: 22200 },
            { period: "2026-09", volume: 27100 },
          ],
          source_image_url: "https://i.pinimg.com/736x/aa/bb/braves.jpg",
          source_pin_url: "https://www.pinterest.com/pin/987654321/",
          picked: true,
          picked_at: "2026-10-05T11:00:00Z",
          favorite: true,
          favorite_at: "2026-10-05T11:00:00Z",
          provider: "aebrowse_google_ads",
          provider_account: "Google Ads account",
          provider_customer_id: "1234567890",
          request_count: 4,
          fetched_at: "2026-10-07T10:00:00Z",
          last_requested_at: "2026-10-07T10:00:00Z",
          created_at: "2026-10-01T10:00:00Z",
          updated_at: "2026-10-07T10:00:00Z",
        }}
        onClose={() => undefined}
      />,
    );

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain("KEYWORD DETAIL · GOOGLE ADS");
    expect(markup).toContain("ATLANTA BRAVES");
    expect(markup).toContain("Avg searches / mo");
    expect(markup).toContain("22,200");
    expect(markup).toContain("-18.1%");
    expect(markup).toContain("+83.1%");
    expect(markup).toContain("Google Ads index 92");
    expect(markup).toContain("Trademark (TM)");
    expect(markup).toContain("Trademark screening has not been returned by AEBrowse");
    expect(markup).toContain("https://tmsearch.uspto.gov/");
    expect(markup).toContain("$0.74");
    expect(markup).toContain("$2.18");
    expect(markup).toContain("Monthly search volume");
    expect(markup).toContain("Jul 2026");
    expect(markup).toContain("27,100");
    expect(markup).toContain('aria-label="Jul 2026: 18,100 searches"');
    expect(markup).toContain("Technical details");
    expect(markup).toContain("Provider account");
    expect(markup).toContain("Google Ads account");
    expect(markup).toContain("1234567890");
    expect(markup).toContain("Open Pinterest source");
  });

  it("renders the source-first UI and removes legacy campaign/candidate/product panels", () => {
    const markup = renderToStaticMarkup(<RealisticReviewUgcPage />);
    expect(markup).toContain("Dual scout pipeline");
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
    expect(markup).toContain("rrugc-source-image-review-modal is-cols-3");
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

  it("uses 1, 2, and 3 explicit source-image columns for small preview groups", () => {
    const plan = makePlan();
    const allSources = plan.source_group_images!;
    for (const count of [1, 2, 3]) {
      const markup = renderToStaticMarkup(
        <SourceImageReviewModal
          plan={plan}
          sources={allSources.slice(0, count)}
          onClose={() => undefined}
        />,
      );
      expect(markup).toContain("rrugc-source-image-review-modal is-cols-" + count);
      expect(markup).toContain("rrugc-source-image-review-masonry is-cols-" + count);
    }
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
    expect(markup).toContain('width="800"');
    expect(markup).toContain('height="1000"');
    expect(markup).toContain("is-pending-ai");
    expect(markup).toContain("Pending analysis");
    expect(markup).toContain("Pinterest ↗");
    expect(markup).toContain(">✓<");
    expect(markup).toContain(">×<");
    expect(markup).toContain("rrugc-source-review-vote is-ai");
    expect(markup).toContain(">AI<");
  });

  it("opens ready Stage 3 review cards into a navigable detail modal", () => {
    const first = makeStage3ReviewImage(
      "review-1",
      "Casey F.",
      "Clean embroidery and an easy everyday look.",
    );
    const second = makeStage3ReviewImage(
      "review-2",
      "Alex P.",
      "The colors feel relaxed and the front design gives the cap just enough personality.",
    );
    const data: Stage3ReviewGroupList = {
      items: [{
        folder_id: "folder-review",
        folder_name: "Bachelorette Hats",
        folder_path: "UGC/Bachelorette Hats",
        image_count: 2,
        status: "ready",
        ready_count: 2,
        rejected_count: 0,
        analyzing_count: 0,
        pending_count: 0,
        error_count: 0,
        latest_completed_at: "2026-10-06T01:00:00Z",
        images: [first, second],
      }],
      total_groups: 1,
      total_images: 2,
      ready_images: 2,
      rejected_images: 0,
      analyzing_images: 0,
      pending_images: 0,
      error_images: 0,
    };

    const galleryMarkup = renderToStaticMarkup(
      <Stage3ReviewGroups
        data={data}
        loading={false}
        analyzing={false}
        message=""
        onAnalyze={() => undefined}
      />,
    );
    expect(galleryMarkup).toContain('role="button"');
    expect(galleryMarkup).toContain("Open review details for Casey F.");

    const modalMarkup = renderToStaticMarkup(
      <Stage3ReviewModal
        entries={[
          { image: first, folderName: "Bachelorette Hats", folderPath: "UGC/Bachelorette Hats" },
          { image: second, folderName: "Bachelorette Hats", folderPath: "UGC/Bachelorette Hats" },
        ]}
        index={0}
        onIndexChange={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(modalMarkup).toContain('role="dialog"');
    expect(modalMarkup).toContain('aria-modal="true"');
    expect(modalMarkup).toContain('aria-label="Previous review"');
    expect(modalMarkup).toContain('aria-label="Next review"');
    expect(modalMarkup).toContain("1 / 2");
    expect(modalMarkup).toContain("Synthetic UGC review");
    expect(modalMarkup).toContain('src="https://img.example/review-1.jpg"');
    expect(modalMarkup).toContain(
      'data-original-src="https://img.example/review-1-original.png"',
    );
    expect(modalMarkup).toContain("Clean embroidery and an easy everyday look.");
  });

  it("keeps progress capped at 100 percent when refs exceed the target", () => {
    expect(sourcePlanProgressPercent({ progress_count: 60, target_count: 50 })).toBe(100);
  });

  it("flags both Review and Keyword Scout clients older than v45", () => {
    expect(scoutClientIsCurrent("rrugc-scout-v42")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v43")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v44")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v45")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v46")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v47")).toBe(false);
    expect(scoutClientIsCurrent("rrugc-scout-v48")).toBe(true);
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

describe("Search Intelligence panel", () => {
  it("shows shared query pool, learned performance and source lane labels", async () => {
    const { SearchIntelligencePanel } = await import("./SearchIntelligencePanel");
    const html = renderToStaticMarkup(
      <SearchIntelligencePanel
        summary={{
          total_queries: 35,
          cycles_completed: 11,
          new_keywords: 9,
          duplicate_pins: 12,
          active_leases: 2,
          lanes: [
            { lane: "suggested", queries: 6, cycles: 4 },
            { lane: "style", queries: 8, cycles: 3 },
            { lane: "product", queries: 15, cycles: 2 },
            { lane: "explore", queries: 6, cycles: 2 },
          ],
          recent: [],
        }}
        loading={false}
        error={null}
      />,
    );
    expect(html).toContain("Search Intelligence");
    expect(html).toContain("Dynamic Query Pool");
    expect(html).toContain(">35<");
    expect(html).toContain(">11<");
    expect(html).toContain(">9<");
    expect(html).toContain(">12<");
    expect(html).toContain(">2<");
    expect(html).toContain('aria-expanded="false"');
  });
});
