import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
import { workspaceRouteForApplicationRoute, requiredPermissionForApplicationRoute } from "../AppRoute";

describe("WorkspaceNavigation permissions", () => {
  it("maps Inventory to its own sidebar route with inventory.read permission", () => {
    expect(workspaceRouteForApplicationRoute("inventory")).toBe("inventory");
    expect(requiredPermissionForApplicationRoute("inventory")).toBe("inventory.read");
  });

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

  it("places Inventory directly after Asset Explorer and keeps its permission independent from AI Operations", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceNavigation active="inventory" showOperations={false} showReviewBoard={false}
        permissions={["inventory.read"]} />
    );
    expect(markup).toContain('href="/inventory"');
    expect(markup.indexOf(">Asset Explorer</span>")).toBeLessThan(markup.indexOf(">Inventory</span>"));
    expect(markup).not.toContain("AI Operations");
    expect(markup).toContain('aria-current="page"');
    const denied = renderToStaticMarkup(
      <WorkspaceNavigation active="assets" permissions={["ai_operations.read"]} showReviewBoard={false} />
    );
    expect(denied).not.toContain('href="/inventory"');
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
    expect(markup).toContain('/realistic-review-ugc?workflow=reviews&amp;stage=reviews-stage2');
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
    expect((markup.match(/workspace-nav-submenu-icon/g) || []).length).toBe(8);
    expect(markup).toContain('aria-current="location"');
  });
});
