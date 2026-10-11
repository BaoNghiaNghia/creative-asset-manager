import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResponsiveWorkspaceNav, availableMobileDestinations, splitMobileDestinations } from "./ResponsiveWorkspaceNav";

describe("responsive mobile workspace navigation", () => {
  it("limits destinations using the same permission gates as the desktop sidebar", () => {
    const viewer = availableMobileDestinations([]);
    expect(viewer.map(item => item.id)).toEqual(["assets", "generation"]);
    expect(availableMobileDestinations(["public_review.read"]).map(item => item.id))
      .toEqual(["assets", "review-board", "generation"]);
    const admin = availableMobileDestinations([
      "ai_operations.read", "realistic_review_ugc.read",
      "public_review.read", "tenant_members.read",
    ]);
    expect(admin.map(item => item.id)).toEqual([
      "assets", "operations", "realistic-review-ugc", "review-board",
      "generation", "queue", "access",
    ]);
  });

  it("keeps at most four discoverable tabs and exposes the rest in More", () => {
    const admin = availableMobileDestinations([
      "ai_operations.read", "realistic_review_ugc.read",
      "public_review.read", "tenant_members.read",
    ]);
    const split = splitMobileDestinations(admin);
    expect(split.tabs).toHaveLength(4);
    expect(split.tabs[0].shortTitle).toBe("Assets");
    expect(split.more.map(item => item.shortTitle)).toEqual(["Video", "Queue", "Access"]);
    expect(splitMobileDestinations(availableMobileDestinations([])).more).toEqual([]);
  });

  it("renders navigable, labeled mobile tabs alongside tablet/desktop nav", () => {
    const html = renderToStaticMarkup(<ResponsiveWorkspaceNav active="assets" />);
    expect(html).toContain('aria-label="Mobile workspace navigation"');
    expect(html).toContain('aria-label="Asset Explorer"');
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('class="workspace-navigation"');
    expect(html).toContain("Assets");
    expect(html).toContain("Video");
  });
});
