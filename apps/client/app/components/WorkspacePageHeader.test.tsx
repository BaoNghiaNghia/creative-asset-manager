import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { WorkspaceRoute } from "./WorkspaceNavigation";
import {
  WORKSPACE_PAGE_HEADERS,
  WorkspaceBackToAssets,
  WorkspacePageHeader,
} from "./WorkspacePageHeader";

describe("WorkspacePageHeader", () => {
  it("defines a shared title contract for every workspace navigation route", () => {
    const routes: WorkspaceRoute[] = [
      "assets",
      "operations",
      "realistic-review-ugc",
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
});
