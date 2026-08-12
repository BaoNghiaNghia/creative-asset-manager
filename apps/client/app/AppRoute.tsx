import App from "./App";
import { AccessManagementPage } from "./access-management/AccessManagementPage";
import { AiOperationsPage } from "./ai-operations/AiOperationsPage";
import { PrivacyPolicyPage, TermsOfServicePage } from "./legal/LegalPages";
import { InventoryApp } from "./inventory/InventoryApp";

export type ApplicationRoute = "explorer" | "ai-operations" | "access-management" | "privacy" | "terms" | "inventory";

export function routeForPath(pathname: string): ApplicationRoute {
  if (pathname.startsWith("/inventory")) return "inventory";
  if (pathname === "/privacy-policy" || pathname === "/privacy") return "privacy";
  if (pathname === "/terms-of-service" || pathname === "/terms") return "terms";
  if (pathname === "/settings/access" || pathname.startsWith("/settings/access/")) return "access-management";
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/")
    ? "ai-operations" : "explorer";
}

export function AppRoute() {
  const route = routeForPath(window.location.pathname);
  return route === "inventory" ? <InventoryApp />
    : route === "privacy" ? <PrivacyPolicyPage />
    : route === "terms" ? <TermsOfServicePage />
    : route === "ai-operations" ? <AiOperationsPage />
    : route === "access-management" ? <AccessManagementPage /> : <App />;
}
