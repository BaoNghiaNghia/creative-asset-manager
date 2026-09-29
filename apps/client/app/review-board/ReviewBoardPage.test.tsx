import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { mayViewReviewBoard } from "../components/WorkspaceNavigation";
import {
  ReviewAssetPreview,
  ReviewBoardPage,
  ReviewIssueInspector,
  ReviewIssueRow,
  groupBoardIssuesByAsset,
} from "./ReviewBoardPage";
import {
  ReviewBoardModeSwitch,
  RrugcReviewInspector,
  RrugcReviewTaskRow,
} from "./RrugcReviewBoardMode";
import type { BoardIssue, BoardIssueDetail } from "./types";
import type { ReviewTask } from "../realistic-review-ugc/types";

const issue: BoardIssue = {
  id: "issue-1",
  status: "open",
  annotation_preview: "Move the embroidery slightly higher.",
  created_at: "2026-09-22T10:57:49Z",
  updated_at: "2026-09-22T11:00:00Z",
  anchor_x: 0.42,
  anchor_y: 0.58,
  reviewer: { display_name: "Guest X15Z" },
  share: { id: "share-1", name: "Desify - Image & Video Assets" },
  asset: {
    asset_id: "asset-1",
    source_asset_id: "source-1",
    filename: "Timeline 20.mp4",
    media_type: "video/mp4",
  },
  reply_count: 2,
  resolved_at: null,
  resolver: null,
};



const rrugcTask: ReviewTask = {
  id: "rrugc-review-1",
  campaign_id: "campaign-1",
  campaign_name: "Pinterest UGC September",
  candidate_id: "candidate-1",
  product_id: "product-1",
  product_sku: "CAP-RED-01",
  product_name: "Red embroidered cap",
  generation_attempt_id: "attempt-1",
  generation_variant: 2,
  supervisor_result_id: "supervisor-1",
  supervisor_status: "needs_human_review",
  supervisor_reason: "HAT_TOO_LARGE",
  supervisor_summary: "Hat scale needs a human check before export.",
  supervisor_metrics: {
    product_visual_similarity: 0.91,
    placement_score: 0.84,
    person_scene_preservation: 0.96,
    artifact_risk: 0.08,
  },
  queue_reason: "supervisor_needs_human_review",
  priority: "high",
  status: "pending",
  review_note: null,
  reviewed_by_user_id: null,
  reviewed_at: null,
  export_status: "pending_review",
  output_url: "/api/v1/realistic-review-ugc/generation-attempts/attempt-1/output",
  created_at: "2026-09-28T00:20:00Z",
  updated_at: "2026-09-28T00:20:00Z",
};


const detail: BoardIssueDetail = {
  ...issue,
  plain_text: "Move the embroidery slightly higher.",
  content_json: {
    type: "doc",
    content: [
      {
        type: "paragraph",
        content: [{ type: "text", text: "Move the embroidery slightly higher." }],
      },
    ],
  },
  replies: [
    {
      id: "reply-1",
      content_json: {
        type: "doc",
        content: [
          {
            type: "paragraph",
            content: [{ type: "text", text: "Will update this." }],
          },
        ],
      },
      plain_text: "Will update this.",
      created_at: "2026-09-22T11:02:00Z",
      updated_at: "2026-09-22T11:02:00Z",
      reviewer: { display_name: "Guest PRNE" },
    },
  ],
};

describe("Review Board boundary", () => {
  it("routes only the review-board path to the authenticated page", () => {
    expect(routeForPath("/review-board")).toBe("review-board");
    expect(routeForPath("/review-board/")).toBe("review-board");
    expect(routeForPath("/share/public-id")).toBe("public-review");
    expect(routeForPath("/share/public-id/folder/1ppjQw5qYj3xEXC3wMa__5Lm8sXiNM16U")).toBe("public-review");
    expect(routeForPath("/job-queue")).toBe("job-queue");
  });

  it("shows Review Board navigation for either supported review source", () => {
    expect(mayViewReviewBoard(["public_review.read"])).toBe(true);
    expect(mayViewReviewBoard(["realistic_review_ugc.read"])).toBe(true);
    expect(mayViewReviewBoard(["public_review.resolve"])).toBe(false);
    expect(mayViewReviewBoard(["realistic_review_ugc.run"])).toBe(false);
    expect(mayViewReviewBoard(["public_review.manage"])).toBe(false);
  });

  it("shows an identity loading state before rendering board data", () => {
    expect(renderToStaticMarkup(<ReviewBoardPage />)).toContain(
      "Loading your Review Board access",
    );
  });
});

describe("Review Board workspace", () => {
  it("groups multiple feedback issues under the same video asset", () => {
    const grouped = groupBoardIssuesByAsset([
      issue,
      {
        ...issue,
        id: "issue-2",
        status: "resolved",
        annotation_preview: "Second note on the same video.",
        reply_count: 1,
        updated_at: "2026-09-22T12:00:00Z",
      },
    ]);
    expect(grouped).toHaveLength(1);
    expect(grouped[0]?.filename).toBe("Timeline 20.mp4");
    expect(grouped[0]?.issues).toHaveLength(2);
    expect(grouped[0]?.openCount).toBe(1);
    expect(grouped[0]?.replyCount).toBe(3);
  });

  it("renders inbox rows with clear status, comment, reviewer and pinned metadata", () => {
    const markup = renderToStaticMarkup(
      <ReviewIssueRow issue={issue} selected onSelect={() => undefined} />,
    );
    expect(markup).toContain("review-board-row selected");
    expect(markup).toContain("Timeline 20.mp4");
    expect(markup).toContain("Move the embroidery slightly higher.");
    expect(markup).toContain("Guest X15Z");
    expect(markup).toContain("Pinned");
    expect(markup).toContain('aria-pressed="true"');
  });

  it("renders the preview as a dedicated workspace with a clear unavailable state", () => {
    const markup = renderToStaticMarkup(
      <ReviewAssetPreview detail={detail} loading={false} />,
    );
    expect(markup).toContain("ASSET PREVIEW");
    expect(markup).toContain("Preview unavailable");
    expect(markup).toContain("Pinned annotation");
    expect(markup).toContain("video/mp4");
  });

  it("renders issue details, replies and the explicit resolve action in the inspector", () => {
    const markup = renderToStaticMarkup(
      <ReviewIssueInspector
        detail={detail}
        loading={false}
        canResolve
        mutating={false}
        mutationError=""
        onTransition={() => undefined}
      />,
    );
    expect(markup).toContain("ISSUE");
    expect(markup).toContain("COMMENT");
    expect(markup).toContain("Replies");
    expect(markup).toContain("Will update this.");
    expect(markup).toContain("Resolve issue");
    expect(markup).not.toContain("Mark Done");
  });
});

describe("Realistic UGC Review Board mode", () => {
  it("renders the source switch without exposing unavailable modes", () => {
    const markup = renderToStaticMarkup(
      <ReviewBoardModeSwitch
        mode="rrugc"
        canShared={false}
        canRrugc
        onMode={() => undefined}
      />,
    );
    expect(markup).toContain("Realistic UGC");
    expect(markup).not.toContain("Shared feedback");
    expect(markup).toContain('aria-selected="true"');
  });

  it("surfaces high-priority Supervisor handoff state in the task row", () => {
    const markup = renderToStaticMarkup(
      <RrugcReviewTaskRow
        task={rrugcTask}
        selected
        onSelect={() => undefined}
      />,
    );
    expect(markup).toContain("High priority");
    expect(markup).toContain("CAP-RED-01");
    expect(markup).toContain("HAT_TOO_LARGE");
    expect(markup).toContain("Supervisor requires human review");
    expect(markup).toContain("Pending review");
  });

  it("renders supervisor metrics and explicit approve/reject actions", () => {
    const markup = renderToStaticMarkup(
      <RrugcReviewInspector
        task={rrugcTask}
        canRun
        note=""
        busy={false}
        error=""
        onNote={() => undefined}
        onApprove={() => undefined}
        onReject={() => undefined}
      />,
    );
    expect(markup).toContain("Needs human review");
    expect(markup).toContain("91%");
    expect(markup).toContain("Approve");
    expect(markup).toContain("Reject");
    expect(markup).toContain("Review note");
  });
});
