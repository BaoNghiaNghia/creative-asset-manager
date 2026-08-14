import { lazy, Suspense } from "react";
import App from "./App";

const AccessManagementPage = lazy(() => import("./access-management/AccessManagementPage").then(module => ({ default: module.AccessManagementPage })));
const AiOperationsPage = lazy(() => import("./ai-operations/AiOperationsPage").then(module => ({ default: module.AiOperationsPage })));
const InventoryApp = lazy(() => import("./inventory/InventoryApp").then(module => ({ default: module.InventoryApp })));
const PrivacyPolicyPage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.PrivacyPolicyPage })));
const TermsOfServicePage = lazy(() => import("./legal/LegalPages").then(module => ({ default: module.TermsOfServicePage })));

export type ApplicationRoute = "explorer" | "ai-operations" | "access-management" | "privacy" | "terms" | "inventory";

export function routeForPath(pathname: string): ApplicationRoute {
  if (pathname.startsWith("/inventory")) return "inventory";
  if (pathname === "/privacy-policy" || pathname === "/privacy") return "privacy";
  if (pathname === "/terms-of-service" || pathname === "/terms") return "terms";
  if (pathname === "/settings/access" || pathname.startsWith("/settings/access/")) return "access-management";
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/") ? "ai-operations" : "explorer";
}

export function AppRoute() {
  const route = routeForPath(window.location.pathname);
  const page = route === "inventory" ? <InventoryApp />
    : route === "privacy" ? <PrivacyPolicyPage />
    : route === "terms" ? <TermsOfServicePage />
    : route === "ai-operations" ? <AiOperationsPage />
    : route === "access-management" ? <AccessManagementPage /> : <App />;
  return <Suspense fallback={<main className="state" aria-busy="true">Loading application...</main>}>{page}</Suspense>;
}
