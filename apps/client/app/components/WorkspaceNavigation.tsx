import type { ReactNode } from "react";

export type WorkspaceRoute = "assets" | "operations" | "queue" | "access";

function WorkspaceNavigationIcon({ name }: { name: WorkspaceRoute }) {
  const paths: Record<WorkspaceRoute, ReactNode> = {
    assets: <><rect x="3" y="5" width="18" height="15" rx="2" /><path d="m4 17 5-5 3.5 3.5 2.5-2.5 5 5M8 9h.01" /></>,
    operations: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></>,
    queue: <><path d="M6 5h14M6 12h14M6 19h14" /><circle cx="3" cy="5" r=".8" fill="currentColor" stroke="none" /><circle cx="3" cy="12" r=".8" fill="currentColor" stroke="none" /><circle cx="3" cy="19" r=".8" fill="currentColor" stroke="none" /></>,
    access: <><circle cx="10" cy="8" r="3.5" /><path d="M3 20c.6-3.5 3-5.5 7-5.5 2.1 0 3.8.6 5 1.8M17 7h4M19 5v4" /></>,
  };
  return <svg className="workspace-nav-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

export function WorkspaceNavigation({ active, showOperations = true }: { active: WorkspaceRoute; showOperations?: boolean }) {
  const items: Array<{ id: WorkspaceRoute; href: string; label: string }> = [
    { id: "assets", href: "/", label: "Asset Explorer" },
    ...(showOperations ? [
      { id: "operations" as const, href: "/ai-operations", label: "AI Operations" },
      { id: "queue" as const, href: "/job-queue", label: "Job Queue" },
    ] : []),
    { id: "access", href: "/settings/access", label: "Access Management" },
  ];
  return <nav className="workspace-navigation" aria-label="Workspace navigation">
    {items.map(item => <a key={item.id} href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
      <WorkspaceNavigationIcon name={item.id} /><span>{item.label}</span>
    </a>)}
  </nav>;
}
