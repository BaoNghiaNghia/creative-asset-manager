import { lazy, Suspense } from "react";
import App from "./App";

const AccessManagementPage = lazy(() => import("./access-management/AccessManagementPage").then(module => ({ default: module.AccessManagementPage })));
const AiOperationsPage = lazy(() => import("./ai-operations/AiOperationsPage").then(module => ({ default: module.AiOperationsPage })));
const JobQueuePage = lazy(() => import("./job-queue/JobQueuePage").then(module => ({ default: module.JobQueuePage })));
const InventoryApp = lazy(() => import("./inventory/InventoryApp").then(module => ({ default: module.InventoryApp })));
const PrivacyPolicyPage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.PrivacyPolicyPage })));
const TermsOfServicePage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.TermsOfServicePage })));
const PublicReviewRoute = lazy(() => import("./public-review/PublicReviewRoute").then(module => ({ default: module.PublicReviewRoute })));
const ReviewBoardPage = lazy(() => import("./review-board/ReviewBoardPage").then(module => ({ default: module.ReviewBoardPage })));
const VideoGenerationPage = lazy(() => import("./video-generation/VideoGenerationPage").then(module => ({ default: module.VideoGenerationPage })));

export type ApplicationRoute = "public-review" | "review-board" | "explorer" | "ai-operations" | "access-management" | "privacy" | "terms" | "inventory" | "job-queue" | "video-generation";

export function routeForPath(pathname: string): ApplicationRoute {
  if (pathname.startsWith("/share/") && /^[A-Za-z0-9_-]{1,128}$/.test(pathname.slice(7))) return "public-review";
  if (pathname === "/review-board" || pathname === "/review-board/") return "review-board";
  if (pathname === "/job-queue") return "job-queue";
  if (pathname === "/video-generation" || pathname.startsWith("/video-generation/")) return "video-generation";
  if (pathname.startsWith("/inventory")) return "inventory";
  if (pathname === "/privacy-policy" || pathname === "/privacy") return "privacy";
  if (pathname === "/terms-of-service" || pathname === "/terms") return "terms";
  if (pathname === "/settings/access" || pathname.startsWith("/settings/access/")) return "access-management";
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/") ? "ai-operations" : "explorer";
}

export function AppRoute() {
  const route = routeForPath(window.location.pathname);
  const page = route === "public-review" ? <PublicReviewRoute /> : route === "review-board" ? <ReviewBoardPage /> : route === "video-generation" ? <VideoGenerationPage />
    : route === "job-queue" ? <JobQueuePage />
    : route === "inventory" ? <InventoryApp />
    : route === "privacy" ? <PrivacyPolicyPage />
    : route === "terms" ? <TermsOfServicePage />
    : route === "ai-operations" ? <AiOperationsPage />
    : route === "access-management" ? <AccessManagementPage /> : <App />;
  return <Suspense fallback={<main className="state" aria-busy="true">Loading application...</main>}>{page}</Suspense>;
}
