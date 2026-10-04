import { lazy, Suspense, useEffect, useState } from "react";
import { fetchAccessIdentity } from "../features/access_management";
import App from "./App";
import { CenteredLoadingState } from "./components/CenteredLoadingState";
import { DesktopUpdateNotice } from "./components/DesktopUpdateNotice";
import { ResponsiveWorkspaceNav } from "./components/ResponsiveWorkspaceNav";
import type { WorkspaceRoute } from "./components/WorkspaceNavigation";

const AccessManagementPage = lazy(() => import("./access-management/AccessManagementPage").then(module => ({ default: module.AccessManagementPage })));
const AiOperationsPage = lazy(() => import("./ai-operations/AiOperationsPage").then(module => ({ default: module.AiOperationsPage })));
const JobQueuePage = lazy(() => import("./job-queue/JobQueuePage").then(module => ({ default: module.JobQueuePage })));
const InventoryApp = lazy(() => import("./inventory/InventoryApp").then(module => ({ default: module.InventoryApp })));
const PrivacyPolicyPage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.PrivacyPolicyPage })));
const TermsOfServicePage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.TermsOfServicePage })));
const PublicReviewRoute = lazy(() => import("./public-review/PublicReviewRoute").then(module => ({ default: module.PublicReviewRoute })));
const ReviewBoardPage = lazy(() => import("./review-board/ReviewBoardPage").then(module => ({ default: module.ReviewBoardPage })));
const RealisticReviewUgcPage = lazy(() => import("./realistic-review-ugc/RealisticReviewUgcPage").then(module => ({ default: module.RealisticReviewUgcPage })));
const VideoGenerationPage = lazy(() => import("./video-generation/VideoGenerationPage").then(module => ({ default: module.VideoGenerationPage })));

export type ApplicationRoute = "public-review" | "review-board" | "explorer" | "ai-operations" | "access-management" | "privacy" | "terms" | "inventory" | "job-queue" | "video-generation" | "realistic-review-ugc";

export function routeForPath(pathname: string): ApplicationRoute {
  if (/^\/share\/[A-Za-z0-9_-]{1,128}(?:\/folder\/[^/]{1,1024})?\/?$/.test(pathname)) return "public-review";
  if (pathname === "/review-board" || pathname === "/review-board/") return "review-board";
  if (pathname === "/job-queue") return "job-queue";
  if (pathname === "/realistic-review-ugc" || pathname === "/realistic-review-ugc/") return "realistic-review-ugc";
  if (pathname === "/video-generation" || pathname.startsWith("/video-generation/")) return "video-generation";
  if (pathname.startsWith("/inventory")) return "inventory";
  if (pathname === "/privacy-policy" || pathname === "/privacy") return "privacy";
  if (pathname === "/terms-of-service" || pathname === "/terms") return "terms";
  if (pathname === "/settings/access" || pathname.startsWith("/settings/access/")) return "access-management";
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/") ? "ai-operations" : "explorer";
}

export function workspaceRouteForApplicationRoute(route: ApplicationRoute): WorkspaceRoute | null {
  if (route === "public-review" || route === "privacy" || route === "terms") return null;
  if (route === "ai-operations" || route === "inventory") return "operations";
  if (route === "job-queue") return "queue";
  if (route === "video-generation") return "generation";
  if (route === "realistic-review-ugc") return "realistic-review-ugc";
  if (route === "review-board") return "review-board";
  if (route === "access-management") return "access";
  return "assets";
}

export function requiredPermissionForApplicationRoute(route: ApplicationRoute): string | null {
  if (route === "ai-operations" || route === "job-queue") return "ai_operations.read";
  if (route === "realistic-review-ugc") return "realistic_review_ugc.read";
  if (route === "access-management") return "tenant_members.read";
  return null;
}

export function AppRoute() {
  const route = routeForPath(window.location.pathname);
  const requiredPermission = requiredPermissionForApplicationRoute(route);
  const [routePermissions, setRoutePermissions] = useState<readonly string[] | null>(
    requiredPermission ? null : [],
  );

  useEffect(() => {
    if (!requiredPermission) {
      setRoutePermissions([]);
      return;
    }
    let alive = true;
    setRoutePermissions(null);
    fetchAccessIdentity()
      .then(identity => { if (alive) setRoutePermissions(identity.permissions); })
      .catch(() => { if (alive) setRoutePermissions([]); });
    return () => { alive = false; };
  }, [requiredPermission]);

  const workspaceRoute = workspaceRouteForApplicationRoute(route);
  const routeAllowed = !requiredPermission || Boolean(routePermissions?.includes(requiredPermission));
  const page = requiredPermission && routePermissions === null
    ? <CenteredLoadingState
      kind="permissions"
      title="Checking permissions…"
      detail="Verifying your workspace access before opening this page."
    />
    : !routeAllowed
      ? <main className="state"><p>This workspace is not available for your role.</p><a href="/">Return to Asset Explorer</a></main>
      : route === "public-review" ? <PublicReviewRoute /> : route === "review-board" ? <ReviewBoardPage /> : route === "video-generation" ? <VideoGenerationPage />
    : route === "realistic-review-ugc" ? <RealisticReviewUgcPage />
    : route === "job-queue" ? <JobQueuePage />
    : route === "inventory" ? <InventoryApp />
    : route === "privacy" ? <PrivacyPolicyPage />
    : route === "terms" ? <TermsOfServicePage />
    : route === "ai-operations" ? <AiOperationsPage />
    : route === "access-management" ? <AccessManagementPage /> : <App />;
  return <>{workspaceRoute && <ResponsiveWorkspaceNav active={workspaceRoute} />}<Suspense fallback={<CenteredLoadingState
    kind="application"
    title="Loading application…"
    detail="Preparing the workspace and interface."
  />}>{page}</Suspense><DesktopUpdateNotice /></>;
}
