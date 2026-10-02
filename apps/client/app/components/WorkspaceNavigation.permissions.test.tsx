import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";

describe("WorkspaceNavigation permissions", () => {
  it("shows Viewer review workflows while hiding admin destinations", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceNavigation
        active="assets"
        showOperations={false}
        showReviewBoard
        permissions={[
          "assets.read",
          "assets.upload",
          "assets.delete",
          "search.read",
          "public_review.read",
          "public_review.resolve",
          "realistic_review_ugc.read",
          "realistic_review_ugc.run",
          "realistic_review_ugc.configure",
        ]}
      />,
    );

    expect(markup).toContain("Asset Explorer");
    expect(markup).toContain("Video Generation");
    expect(markup).toContain("Realistic Review UGC");
    expect(markup).toContain("Review Board");
    expect(markup).not.toContain("AI Operations");
    expect(markup).not.toContain("Job Queue");
    expect(markup).not.toContain("Access Management");
  });

  it("shows privileged destinations when permissions allow them", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceNavigation
        active="assets"
        showOperations
        showReviewBoard={false}
        permissions={["ai_operations.read", "realistic_review_ugc.read", "tenant_members.read"]}
      />,
    );

    expect(markup).toContain("AI Operations");
    expect(markup).toContain("Job Queue");
    expect(markup).toContain("Realistic Review UGC");
    expect(markup).toContain("Access Management");
  });
});
