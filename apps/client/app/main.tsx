import React, { useEffect } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/be-vietnam-pro/400.css";
import "@fontsource/be-vietnam-pro/400-italic.css";
import "@fontsource/be-vietnam-pro/500.css";
import "@fontsource/be-vietnam-pro/600.css";
import "@fontsource/be-vietnam-pro/700.css";
import "@fontsource/be-vietnam-pro/800.css";
import "@fontsource/be-vietnam-pro/900.css";
import { AppRoute } from "./AppRoute";
import { ActionToastViewport } from "./components/ActionToast";
import "../styles/global.css";
import "../styles/centered-loading.css";
import "../styles/ai-operations.css";
import "../styles/access-management.css";
import "../styles/inventory.css";
// Public review and Review Board styles are loaded with their lazy routes.
// Do not ship either full media stylesheet to every CAM workspace.
import "../styles/workspace-page-header.css";
import "../styles/responsive-platform.css";
import "../styles/ui-foundation.css";
import "../styles/mobile-workspace.css";
import "../styles/rrugc-sidebar-navigation.css";
// Phase 4: scoped evondevKit roles for Explorer, Queue, Inventory and Access.
import "../app/styles/cam-evo-tokens.css";
import "../styles/cam-console-evo.css";
// Phase 5: route-scoped evondevKit roles, no changes to queues or routes.
import "../styles/cam-platform-evo.css";
// Phase 6: shared loading, empty/error, retry and modal feedback styling.
import "../styles/cam-feedback-evo.css";

function AppBoot() {
  useEffect(() => {
    document.documentElement.dataset.camReady = "1";
    return () => {
      delete document.documentElement.dataset.camReady;
    };
  }, []);

  return <><AppRoute /><ActionToastViewport /></>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppBoot />
  </React.StrictMode>,
);
