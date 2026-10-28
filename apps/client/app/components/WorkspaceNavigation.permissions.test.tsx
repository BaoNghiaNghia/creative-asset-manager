import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
import { workspaceRouteForApplicationRoute, requiredPermissionForApplicationRoute } from "../AppRoute";

describe("WorkspaceNavigation permissions", () => {
  it("maps Inventory to its own sidebar route with inventory.read permission", () => {
    expect(workspaceRouteForApplicationRoute("inventory")).toBe("inventory");
    expect(requiredPermissionForApplicationRoute("inventory")).toBe("inventory.read");
  });

  it("shows only the Inventory overview entry, without obsolete technical subpages", () => {
    const markup = renderToStaticMarkup(<WorkspaceNavigation active="inventory" permissions={["inventory.read"]} showOperations={false} showReviewBoard={false} />);
    expect(markup).toContain('href="/inventory"');
    expect(markup).not.toContain('workspace-inventory-submenu');
    expect(markup).not.toContain('Expand Inventory navigation');
    expect(markup).not.toContain('Collapse Inventory navigation');
    for (const path of ["pipeline", "knowledge", "materials", "prompts", "settings"]) {
      expect(markup).not.toContain(`href="/inventory/${path}"`);
    }
    const denied = renderToStaticMarkup(<WorkspaceNavigation active="assets" permissions={[]} showOperations={false} showReviewBoard={false} />);
    expect(denied).not.toContain('href="/inventory"');
  });

  it("scopes identical shared permission keys to the explicitly granted navbar", () => {
    const branding = renderToStaticMarkup(
      <WorkspaceNavigation active="branding-dna"
        permissions={["ai_operations.read", "ai_provider.configure"]}
        navigationModules={["branding-dna"]} showOperations showReviewBoard={false} />,
    );
    expect(branding).toContain('href="/branding-dna"');
    expect(branding).not.toContain('href="/ai-operations"');
    expect(branding).not.toContain('href="/job-queue"');
    expect(branding).not.toContain('href="/inventory"');
    const jobs = renderToStaticMarkup(
      <WorkspaceNavigation active="queue" permissions={["ai_operations.read", "ai_jobs.retry"]}
        navigationModules={["queue"]} showOperations showReviewBoard={false} />,
    );
    expect(jobs).toContain('href="/job-queue"');
    expect(jobs).not.toContain('href="/ai-operations"');
    expect(jobs).not.toContain('href="/branding-dna"');
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
    expect(markup).not.toContain("Branding DNA");
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
    expect(markup).toContain("Branding DNA");
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
    expect(markup).toContain('/realistic-review-ugc?workflow=reviews&amp;stage=reviews-stage1');
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
    expect((markup.match(/workspace-nav-submenu-icon/g) || []).length).toBeGreaterThan(0);
    expect(markup).toContain('aria-current="location"');
  });
});
