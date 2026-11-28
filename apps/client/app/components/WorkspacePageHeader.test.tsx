import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { WorkspaceRoute } from "./WorkspaceNavigation";
import { WorkspaceAccountContext } from "./WorkspaceAccountAvatar";
import {
  WORKSPACE_PAGE_HEADERS,
  WorkspaceBackToAssets,
  WorkspacePageHeader,
} from "./WorkspacePageHeader";

describe("WorkspacePageHeader", () => {
  it("defines a shared title contract for every workspace navigation route", () => {
    const routes: WorkspaceRoute[] = [
      "assets",
      "inventory",
      "operations",
      "realistic-review-ugc",
      "tiktokshop",
      "etsy-spy",
      "branding-dna",
      "queue",
      "generation",
      "review-board",
      "access",
    ];
    expect(Object.keys(WORKSPACE_PAGE_HEADERS).sort()).toEqual([...routes].sort());
    for (const route of routes) {
      expect(WORKSPACE_PAGE_HEADERS[route].title.trim()).not.toBe("");
      expect(WORKSPACE_PAGE_HEADERS[route].eyebrow.trim()).not.toBe("");
      expect(WORKSPACE_PAGE_HEADERS[route].description.trim()).not.toBe("");
    }
  });

  it("renders the same title structure with optional page actions", () => {
    const markup = renderToStaticMarkup(
      <WorkspacePageHeader
        route="queue"
        className="job-queue-header"
        actionsClassName="job-queue-header-actions"
        actions={<WorkspaceBackToAssets />}
      />,
    );

    expect(markup).toContain('class="workspace-page-header job-queue-header"');
    expect(markup).toContain('class="workspace-page-header-actions job-queue-header-actions"');
    expect(markup).toContain(">Operations</small>");
    expect(markup).toContain("Job Queue");
    expect(markup).toContain("Monitor Generate Square 1:1 jobs");
    expect(markup).toContain("Back to assets");
  });

  it("places the authenticated account avatar after all actions on the requested workspaces", () => {
    const account = { display_name: "Test Account", email: "test@example.com", avatar_url: "https://lh3.googleusercontent.com/test-avatar" };
    for (const route of ["etsy-spy", "tiktokshop", "realistic-review-ugc"] as const) {
      const html = renderToStaticMarkup(<WorkspaceAccountContext.Provider value={account}>
        <WorkspacePageHeader route={route} actions={<button>Last action</button>} />
      </WorkspaceAccountContext.Provider>);
      expect(html).toContain("workspace-page-header-has-avatar");
      expect(html).toContain('src="https://lh3.googleusercontent.com/test-avatar"');
      expect(html).toContain('referrerPolicy="no-referrer"');
      expect(html.indexOf("Last action")).toBeLessThan(html.indexOf('class="workspace-account-avatar"'));
    }
    expect(renderToStaticMarkup(<WorkspacePageHeader route="etsy-spy" />)).not.toContain('class="workspace-account-avatar"');
    expect(renderToStaticMarkup(<WorkspaceAccountContext.Provider value={account}>
      <WorkspacePageHeader route="assets" />
    </WorkspaceAccountContext.Provider>)).not.toContain('class="workspace-account-avatar"');
  });

  it("shows initials if the authenticated user has no image", () => {
    const html = renderToStaticMarkup(<WorkspaceAccountContext.Provider value={{display_name:"Test Account"}}>
      <WorkspacePageHeader route="tiktokshop" />
    </WorkspaceAccountContext.Provider>);
    expect(html).toContain('class="workspace-account-avatar-initials">TE');
  });

  it("allows an operational page to override its heading without changing shared actions", () => {
    const markup = renderToStaticMarkup(<WorkspacePageHeader route="inventory"
      className="cam-header-v18" eyebrow="Inventory · Operations"
      title="Tổng quan kiểm kê tự động" description="Theo dõi chu kỳ"
      titleAddon={<span>Hoạt động bình thường</span>}
      actions={<WorkspaceBackToAssets />} />);
    expect(markup).toContain("workspace-page-header cam-header-v18");
    expect(markup).toContain("Tổng quan kiểm kê tự động");
    expect(markup).toContain("Inventory · Operations");
    expect(markup).toContain("Hoạt động bình thường");
    expect(markup).toContain("Back to assets");
    expect(markup).not.toContain(">Inventory</h1>");
  });
});
