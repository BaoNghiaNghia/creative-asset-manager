import { describe, expect, it } from "vitest";
import { requiredPermissionForApplicationRoute, routeForPath, workspaceRouteForApplicationRoute } from "./AppRoute";

describe("responsive workspace routing", () => {
  it("maps internal application routes to the shared workspace navigation", () => {
    expect(workspaceRouteForApplicationRoute("explorer")).toBe("assets");
    expect(workspaceRouteForApplicationRoute("ai-operations")).toBe("operations");
    expect(workspaceRouteForApplicationRoute("inventory")).toBe("operations");
    expect(workspaceRouteForApplicationRoute("job-queue")).toBe("queue");
    expect(workspaceRouteForApplicationRoute("video-generation")).toBe("generation");
    expect(workspaceRouteForApplicationRoute("realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(workspaceRouteForApplicationRoute("review-board")).toBe("review-board");
    expect(workspaceRouteForApplicationRoute("access-management")).toBe("access");
  });

  it("does not show workspace navigation on public or legal routes", () => {
    expect(workspaceRouteForApplicationRoute("public-review")).toBeNull();
    expect(workspaceRouteForApplicationRoute("privacy")).toBeNull();
    expect(workspaceRouteForApplicationRoute("terms")).toBeNull();
  });

  it("keeps route parsing compatible with responsive navigation", () => {
    expect(routeForPath("/")).toBe("explorer");
    expect(routeForPath("/ai-operations")).toBe("ai-operations");
    expect(routeForPath("/settings/access")).toBe("access-management");
  });

  it("gates privileged workspaces before their page can issue protected API calls", () => {
    expect(requiredPermissionForApplicationRoute("ai-operations")).toBe("ai_operations.read");
    expect(requiredPermissionForApplicationRoute("job-queue")).toBe("ai_operations.read");
    expect(requiredPermissionForApplicationRoute("realistic-review-ugc")).toBe("realistic_review_ugc.read");
    expect(requiredPermissionForApplicationRoute("access-management")).toBe("tenant_members.read");
    expect(requiredPermissionForApplicationRoute("explorer")).toBeNull();
    expect(requiredPermissionForApplicationRoute("video-generation")).toBeNull();
  });
});
