import App from "./App";
import { AccessManagementPage } from "./access-management/AccessManagementPage";
import { AiOperationsPage } from "./ai-operations/AiOperationsPage";

export type ApplicationRoute = "explorer" | "ai-operations" | "access-management";

export function routeForPath(pathname: string): ApplicationRoute {
  if (pathname === "/settings/access" || pathname.startsWith("/settings/access/")) return "access-management";
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/")
    ? "ai-operations" : "explorer";
}

export function AppRoute() {
  const route = routeForPath(window.location.pathname);
  return route === "ai-operations" ? <AiOperationsPage />
    : route === "access-management" ? <AccessManagementPage /> : <App />;
}
