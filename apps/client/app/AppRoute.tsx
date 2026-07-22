import App from "./App";
import { AiOperationsPage } from "./ai-operations/AiOperationsPage";

export type ApplicationRoute = "explorer" | "ai-operations";

export function routeForPath(pathname: string): ApplicationRoute {
  return pathname === "/ai-operations" || pathname.startsWith("/ai-operations/")
    ? "ai-operations" : "explorer";
}

export function AppRoute() {
  return routeForPath(window.location.pathname) === "ai-operations"
    ? <AiOperationsPage /> : <App />;
}
