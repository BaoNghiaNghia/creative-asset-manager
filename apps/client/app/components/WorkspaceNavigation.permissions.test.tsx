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

  it("places the GatorHats Products and Reviews links directly under Realistic Review UGC", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceNavigation
        active="realistic-review-ugc"
        rrugcStage="stage4"
        showOperations={false}
        showReviewBoard={false}
        permissions={["realistic_review_ugc.read"]}
      />,
    );
    expect(markup).toContain('id="workspace-rrugc-submenu"');
    expect(markup).toContain('aria-label="Collapse Realistic Review UGC navigation"');
    expect(markup).toContain('data-rrugc-workflow="products"');
    expect(markup).toContain('data-rrugc-workflow="reviews"');
    expect(markup).toContain('/realistic-review-ugc?workflow=reviews&amp;stage=stage3');
    expect(markup).toMatch(/aria-current="location"[^>]*data-rrugc-workflow="reviews"/);
    expect(markup).not.toContain("GatorHats workflows");
  });

  it("does not expose GatorHats links to users without RRUGC permission", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceNavigation active="assets" showOperations={false} showReviewBoard={false} permissions={[]} />,
    );
    expect(markup).not.toContain("GatorHats Products");
    expect(markup).not.toContain("GatorHats Reviews");
  });

  it("renders the redesigned AI Operations submenu icon system and vector expander", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceNavigation
        active="operations"
        aiOperationsTab="processing"
        showOperations
        showReviewBoard={false}
        permissions={["ai_operations.read", "realistic_review_ugc.read", "tenant_members.read"]}
      />,
    );

    expect(markup).toContain('class="workspace-nav-expander-icon"');
    expect(markup).toContain("Pipeline overview");
    expect(markup).toContain("Configuration");
    expect((markup.match(/workspace-nav-submenu-icon/g) || []).length).toBe(9);
    expect(markup).toContain('aria-current="location"');
  });
});
