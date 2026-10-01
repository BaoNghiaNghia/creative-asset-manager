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
import "../styles/global.css";
import "../styles/ai-operations.css";
import "../styles/access-management.css";
import "../styles/inventory.css";
import "../styles/public-review.css";
import "../styles/review-board.css";
import "../styles/workspace-page-header.css";
import "../styles/responsive-platform.css";

function AppBoot() {
  useEffect(() => {
    document.documentElement.dataset.camReady = "1";
    return () => {
      delete document.documentElement.dataset.camReady;
    };
  }, []);

  return <AppRoute />;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppBoot />
  </React.StrictMode>,
);
