import React from "react";
import { createRoot } from "react-dom/client";
import { AppRoute } from "./AppRoute";
import "../styles/global.css";
import "../styles/ai-operations.css";
import "../styles/access-management.css";
createRoot(document.getElementById("root")!).render(<React.StrictMode><AppRoute /></React.StrictMode>);
