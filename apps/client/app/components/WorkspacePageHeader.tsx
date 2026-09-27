import type { ReactNode } from "react";
import type { WorkspaceRoute } from "./WorkspaceNavigation";

type WorkspacePageHeaderDefinition = {
  eyebrow: string;
  title: string;
  description: string;
};

/**
 * Every WorkspaceNavigation route must have a title definition here.
 * The Record<WorkspaceRoute, ...> intentionally makes adding a future navbar
 * route fail typecheck until its shared page-title contract is defined.
 */
export const WORKSPACE_PAGE_HEADERS: Record<WorkspaceRoute, WorkspacePageHeaderDefinition> = {
  assets: {
    eyebrow: "Assets",
    title: "Asset Explorer",
    description: "Browse, search, and manage creative assets across connected sources.",
  },
  operations: {
    eyebrow: "Operations",
    title: "AI Operations",
    description: "Pipeline progress, AI analysis, usage and cost for the current tenant.",
  },
  "realistic-review-ugc": {
    eyebrow: "Reference automation",
    title: "Realistic Review UGC",
    description: "Scan Pinterest with a local authenticated Browser Scout and save references into CAM Managed Google Drive.",
  },
  queue: {
    eyebrow: "Operations",
    title: "Job Queue",
    description: "Monitor Generate Square 1:1 jobs, retries, failures, and completed images.",
  },
  generation: {
    eyebrow: "Creation",
    title: "Video Generation",
    description: "Generate a managed MP4 from a prompt and optional CAM image references.",
  },
  "review-board": {
    eyebrow: "Public review",
    title: "Review Board",
    description: "Review and resolve feedback across shared assets.",
  },
  access: {
    eyebrow: "Settings",
    title: "Access Management",
    description: "Manage tenant members, roles, and your active workspace.",
  },
};

type WorkspacePageHeaderProps = {
  route: WorkspaceRoute;
  description?: ReactNode;
  titleAddon?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  actionsClassName?: string;
  className?: string;
};

export function WorkspacePageHeader({
  route,
  description,
  titleAddon,
  meta,
  actions,
  actionsClassName,
  className,
}: WorkspacePageHeaderProps) {
  const definition = WORKSPACE_PAGE_HEADERS[route];
  return <header className={["workspace-page-header", className || ""].filter(Boolean).join(" ")}>
    <div className="workspace-page-header-copy">
      <small className="workspace-page-header-eyebrow">{definition.eyebrow}</small>
      <div className="workspace-page-header-title-row">
        <h1>{definition.title}</h1>
        {titleAddon}
      </div>
      {(description ?? definition.description) && <p className="workspace-page-header-description">{description ?? definition.description}</p>}
      {meta && <div className="workspace-page-header-meta">{meta}</div>}
    </div>
    {actions && <div className={["workspace-page-header-actions", actionsClassName || ""].filter(Boolean).join(" ")}>{actions}</div>}
  </header>;
}

export function WorkspaceBackToAssets() {
  return <a className="workspace-page-back-link" href="/">← Back to assets</a>;
}
