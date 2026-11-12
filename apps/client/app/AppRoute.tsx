import { lazy, Suspense, useEffect, useState } from "react";
import { useRouteAccessGate } from "./routePermissionGate";
import { useDeploymentReadiness } from "./deploymentReadiness";
import { UnderConstructionPage } from "./components/UnderConstructionPage";
import { WorkspaceConnectionState } from "./components/WorkspaceConnectionState";
import App from "./App";
import { CenteredLoadingState } from "./components/CenteredLoadingState";
import { DesktopUpdateNotice } from "./components/DesktopUpdateNotice";
import { ResponsiveWorkspaceNav } from "./components/ResponsiveWorkspaceNav";
import { WorkspaceNavigation } from "./components/WorkspaceNavigation";
import "./styles/workspace-backend-status.css";

import type { WorkspaceRoute } from "./components/WorkspaceNavigation";

const AccessManagementPage = lazy(() => import("./access-management/AccessManagementPage").then(module => ({ default: module.AccessManagementPage })));
const AiOperationsPage = lazy(() => import("./ai-operations/AiOperationsPage").then(module => ({ default: module.AiOperationsPage })));
const JobQueuePage = lazy(() => import("./job-queue/JobQueuePage").then(module => ({ default: module.JobQueuePage })));
const BrandingDnaPage = lazy(() => import("./branding-dna/BrandingDnaPage").then(module => ({ default: module.BrandingDnaPage })));

const InventoryOverviewPage = lazy(() => import("./inventory/InventoryOverviewPage").then(module => ({ default: module.InventoryOverviewPage })));
const PrivacyPolicyPage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.PrivacyPolicyPage })));
const TermsOfServicePage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.TermsOfServicePage })));
const PublicReviewRoute = lazy(() => import("./public-review/PublicReviewRoute").then(module => ({ default: module.PublicReviewRoute })));
const ReviewBoardPage = lazy(() => import("./review-board/ReviewBoardPage").then(module => ({ default: module.ReviewBoardPage })));
const RealisticReviewUgcPage = lazy(() => import("./realistic-review-ugc/RealisticReviewUgcPage").then(module => ({ default: module.RealisticReviewUgcPage })));
const TiktokShopScoutUgcPage = lazy(() => import("./tiktokshop/TiktokShopScoutUgcPage").then(module => ({ default: module.TiktokShopScoutUgcPage })));
const EtsySpyPage = lazy(() => import("./etsy-spy/EtsySpyPage").then(module => ({ default: module.EtsySpyPage })));
const VideoGenerationPage = lazy(() => import("./video-generation/VideoGenerationPage").then(module => ({ default: module.VideoGenerationPage })));

export type ApplicationRoute = "public-review" | "review-board" | "explorer" | "ai-operations" | "access-management" | "privacy" | "terms" | "inventory" | "job-queue" | "video-generation" | "realistic-review-ugc" | "tiktokshop-scout-ugc" | "etsy-spy" | "branding-dna" | "under-construction";

export function routeForPath(pathname: string): ApplicationRoute {
  if (/^\/share\/[A-Za-z0-9_-]{1,128}(?:\/folder\/[^/]{1,1024})?\/?$/.test(pathname)) return "public-review";
  if (pathname === "/under-construction" || pathname === "/under-construction/") return "under-construction";
  if (pathname === "/review-board" || pathname === "/review-board/") return "review-board";
  if (pathname === "/job-queue") return "job-queue";
  // Preserve bookmarks from the former Branding Character route.
  if (pathname === "/branding-dna" || pathname.startsWith("/branding-dna/")
    || pathname === "/branding-character" || pathname.startsWith("/branding-character/")) return "branding-dna";
  if (pathname === "/realistic-review-ugc" || pathname === "/realistic-review-ugc/") return "realistic-review-ugc";
  if (pathname === "/tiktokshop/scout-ugc" || pathname === "/tiktokshop/scout-ugc/") return "tiktokshop-scout-ugc";
  if (pathname === "/etsy-spy" || pathname === "/etsy-spy/") return "etsy-spy";
  if (pathname === "/video-generation" || pathname.startsWith("/video-generation/")) return "video-generation";
  if (pathname.startsWith("/inventory")) return "inventory";
  if (pathname === "/privacy-policy" || pathname === "/privacy") return "privacy";
  if (pathname === "/terms-of-service" || pathname === "/terms") return "terms";
  if (pathname === "/settings/access" || pathname.startsWith("/settings/access/")) return "access-management";
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/") ? "ai-operations" : "explorer";
}

export function workspaceRouteForApplicationRoute(route: ApplicationRoute): WorkspaceRoute | null {
  if (route === "public-review" || route === "privacy" || route === "terms" || route === "under-construction") return null;
  if (route === "inventory") return "inventory";
  if (route === "ai-operations") return "operations";
  if (route === "branding-dna") return "branding-dna";
  if (route === "job-queue") return "queue";
  if (route === "video-generation") return "generation";
  if (route === "realistic-review-ugc") return "realistic-review-ugc";
  if (route === "tiktokshop-scout-ugc") return "tiktokshop";
  if (route === "etsy-spy") return "etsy-spy";
  if (route === "review-board") return "review-board";
  if (route === "access-management") return "access";
  return "assets";
}

/** Reuse CAM's permission-aware desktop sidebar on workspace routes that
 * need persistent left navigation. Tablets and phones use ResponsiveWorkspaceNav. */
export function usesDesktopWorkspaceSidebar(route: ApplicationRoute): boolean {
  return route === "inventory" || route === "branding-dna" || route === "etsy-spy";
}

export function requiredPermissionForApplicationRoute(route: ApplicationRoute): string | null {
  if (route === "ai-operations" || route === "job-queue" || route === "branding-dna") return "ai_operations.read";
  if (route === "inventory") return "inventory.read";
  if (route === "realistic-review-ugc" || route === "tiktokshop-scout-ugc") return "realistic_review_ugc.read";
  if (route === "etsy-spy") return "etsy_spy.read";
  if (route === "access-management") return "tenant_members.read";
  return null;
}

/** An authorized Etsy Spy workspace retains dialogs during brief API downtime. */
export function shouldUnmountForMaintenance(
  route: ApplicationRoute, accessStatus: string, backendUnavailable: boolean,
): boolean {
  return accessStatus === "maintenance" || (backendUnavailable && route !== "etsy-spy");
}

export function AppRoute() {
  const legacyInventoryTab = window.location.pathname === "/ai-operations"
    && new URLSearchParams(window.location.search).get("tab") === "inventory";
  const legacyBrandingPath = window.location.pathname === "/branding-character"
    || window.location.pathname.startsWith("/branding-character/");
  const route = routeForPath(legacyInventoryTab ? "/inventory" : window.location.pathname);
  const requiredPermission = requiredPermissionForApplicationRoute(route);
  const workspaceRoute = workspaceRouteForApplicationRoute(route);
  useEffect(() => {
    if (legacyInventoryTab) window.history.replaceState(window.history.state, "", "/inventory");
    else if (legacyBrandingPath) {
      const canonical = "/branding-dna" + window.location.pathname.slice("/branding-character".length);
      window.history.replaceState(window.history.state, "", canonical + window.location.search + window.location.hash);
    }
  }, [legacyInventoryTab, legacyBrandingPath]);
  const [accessRetryKey, setAccessRetryKey] = useState(0);
  const access = useRouteAccessGate(requiredPermission, undefined, accessRetryKey, workspaceRoute);
  const watchBackend = route !== "public-review" && route !== "privacy"
    && route !== "terms" && route !== "under-construction"
    && (!requiredPermission || access.status === "allowed");
  const backendUnavailable = useDeploymentReadiness(watchBackend);
  // Never unmount an already-authorized Etsy Spy workspace during temporary
  // backend downtime. Its open galleries, Listing Optimizer and scroll survive.
  // Routes without this in-place recovery keep the existing maintenance gate.
  if (shouldUnmountForMaintenance(route, access.status, backendUnavailable)) {
    return <UnderConstructionPage maintenance />;
  }
  if (access.status === "reconnecting" || access.status === "unauthenticated" || access.status === "denied") {
    return <WorkspaceConnectionState mode={access.status} onRetry={() => setAccessRetryKey(value => value + 1)} />;
  }

  const routeAllowed = !requiredPermission || access.status === "allowed";
  const page = requiredPermission && access.status === "checking"
    ? <CenteredLoadingState
      kind="permissions"
      title="Checking permissions…"
      detail="Verifying your workspace access before opening this page."
    />
    : !routeAllowed
      ? <WorkspaceConnectionState mode="denied" />
      : route === "under-construction" ? <UnderConstructionPage /> : route === "public-review" ? <PublicReviewRoute /> : route === "review-board" ? <ReviewBoardPage /> : route === "video-generation" ? <VideoGenerationPage />
    : route === "realistic-review-ugc" ? <RealisticReviewUgcPage />
    : route === "tiktokshop-scout-ugc" ? <TiktokShopScoutUgcPage />
    : route === "etsy-spy" ? <EtsySpyPage />
    : route === "job-queue" ? <JobQueuePage />
    : route === "branding-dna" ? <BrandingDnaPage />
    : route === "inventory" ? <InventoryOverviewPage />
    : route === "privacy" ? <PrivacyPolicyPage />
    : route === "terms" ? <TermsOfServicePage />
    : route === "ai-operations" ? <AiOperationsPage />
    : route === "access-management" ? <AccessManagementPage /> : <App />;
  return <>{workspaceRoute && <ResponsiveWorkspaceNav active={workspaceRoute} />}{usesDesktopWorkspaceSidebar(route) && workspaceRoute ? <aside className="inventory-desktop-sidebar" aria-label={route === "etsy-spy" ? "Etsy Spy workspace sidebar" : route === "branding-dna" ? "Branding DNA workspace sidebar" : "Inventory workspace sidebar"}><a className="inventory-sidebar-brand" href="/"><img src="/app-icon-192.png" alt="" /> <span>Creative assets <small>Operations console</small></span></a><WorkspaceNavigation active={workspaceRoute} /></aside> : null}<div className={usesDesktopWorkspaceSidebar(route) ? (route === "etsy-spy" ? "inventory-route-main etsy-spy-route-main" : "inventory-route-main") : "app-route-content"}><Suspense fallback={<CenteredLoadingState
    kind="application"
    title="Loading application…"
    detail="Preparing the workspace and interface."
  />}>{page}</Suspense></div><DesktopUpdateNotice />{backendUnavailable && route === "etsy-spy" &&
    <div role="status" aria-live="polite" className="workspace-backend-connection-banner">
      CAM đang kết nối lại máy chủ. Dữ liệu hiện tại và cửa sổ đang mở được giữ nguyên.
    </div>}</>;
}
