import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { mayViewReviewBoard } from "../components/WorkspaceNavigation";
import {
  ReviewAssetPreview,
  ReviewBoardPage,
  ReviewIssueInspector,
  ReviewIssueRow,
} from "./ReviewBoardPage";
import type { BoardIssue, BoardIssueDetail } from "./types";

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
    expect(routeForPath("/job-queue")).toBe("job-queue");
  });

  it("uses the exact read permission for navigation visibility", () => {
    expect(mayViewReviewBoard(["public_review.read"])).toBe(true);
    expect(mayViewReviewBoard(["public_review.resolve"])).toBe(false);
    expect(mayViewReviewBoard(["public_review.manage"])).toBe(false);
  });

  it("shows an identity loading state before rendering board data", () => {
    expect(renderToStaticMarkup(<ReviewBoardPage />)).toContain(
      "Loading your Review Board access",
    );
  });
});

describe("Review Board workspace", () => {
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
