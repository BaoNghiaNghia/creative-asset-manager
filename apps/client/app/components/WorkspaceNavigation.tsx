import { useEffect, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import accessManagementIcon from "../../assets/navigation/access-management.svg";
import aiOperationsIcon from "../../assets/navigation/ai-operations.svg";
import assetExplorerIcon from "../../assets/navigation/asset-explorer.svg";
import jobQueueIcon from "../../assets/navigation/job-queue.svg";
import reviewBoardIcon from "../../assets/navigation/review-board.svg";
import videoGenerationIcon from "../../assets/navigation/video-generation.svg";
import {
  AI_OPERATIONS_TABS,
  aiOperationsTabHref,
  type AiOpsTab,
} from "../ai-operations/navigation";

export type WorkspaceRoute = "assets" | "operations" | "queue" | "generation" | "review-board" | "access";

export const mayViewReviewBoard = (permissions: readonly string[]) => permissions.includes("public_review.read");

const navigationIconSources: Record<WorkspaceRoute, string> = {
  assets: assetExplorerIcon,
  operations: aiOperationsIcon,
  queue: jobQueueIcon,
  generation: videoGenerationIcon,
  "review-board": reviewBoardIcon,
  access: accessManagementIcon,
};

function WorkspaceNavigationIcon({ name }: { name: WorkspaceRoute }) {
  return <img className="workspace-nav-icon" src={navigationIconSources[name]} alt="" aria-hidden="true" />;
}

export function WorkspaceNavigation({
  active,
  showOperations = true,
  showReviewBoard,
  aiOperationsTab,
  onAiOperationsTab,
}: {
  active: WorkspaceRoute;
  showOperations?: boolean;
  showReviewBoard?: boolean;
  aiOperationsTab?: AiOpsTab;
  onAiOperationsTab?: (tab: AiOpsTab) => void;
}) {
  const [visible, setVisible] = useState(showReviewBoard ?? false);
  const [operationsOpen, setOperationsOpen] = useState(active === "operations");

  useEffect(() => {
    if (showReviewBoard !== undefined) {
      setVisible(showReviewBoard);
      return;
    }
    let alive = true;
    fetchAccessIdentity()
      .then(identity => { if (alive) setVisible(mayViewReviewBoard(identity.permissions)); })
      .catch(() => { if (alive) setVisible(false); });
    return () => { alive = false; };
  }, [showReviewBoard]);

  useEffect(() => {
    if (active === "operations") setOperationsOpen(true);
  }, [active]);

  const items: Array<{ id: WorkspaceRoute; href: string; label: string }> = [
    { id: "assets", href: "/", label: "Asset Explorer" },
    ...(showOperations ? [
      { id: "operations" as const, href: "/ai-operations", label: "AI Operations" },
      { id: "queue" as const, href: "/job-queue", label: "Job Queue" },
    ] : []),
    { id: "generation", href: "/video-generation", label: "Video Generation" },
    ...(visible ? [{ id: "review-board" as const, href: "/review-board", label: "Review Board" }] : []),
    { id: "access", href: "/settings/access", label: "Access Management" },
  ];

  return <nav className="workspace-navigation" aria-label="Workspace navigation">
    {items.map(item => item.id === "operations" ? <div key={item.id} className={"workspace-nav-group" + (active === "operations" ? " active" : "")}>
      <div className="workspace-nav-group-row">
        <a href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
          <WorkspaceNavigationIcon name={item.id} />
          <span>{item.label}</span>
        </a>
        <button
          type="button"
          className="workspace-nav-expander"
          aria-label={(operationsOpen ? "Collapse" : "Expand") + " AI Operations navigation"}
          aria-expanded={operationsOpen}
          aria-controls="workspace-ai-operations-submenu"
          onClick={() => setOperationsOpen(value => !value)}
        >
          <span aria-hidden="true">⌄</span>
        </button>
      </div>
      {operationsOpen ? <div id="workspace-ai-operations-submenu" className="workspace-nav-submenu" aria-label="AI Operations sections">
        {AI_OPERATIONS_TABS.map(tab => {
          const selected = active === "operations" && aiOperationsTab === tab.id;
          return <a
            key={tab.id}
            href={aiOperationsTabHref(tab.id)}
            className={selected ? "active" : undefined}
            aria-current={selected ? "location" : undefined}
            data-ai-operations-tab={tab.id}
            onClick={event => {
              if (!onAiOperationsTab) return;
              event.preventDefault();
              onAiOperationsTab(tab.id);
            }}
          >
            <img className="workspace-nav-submenu-icon" src={tab.iconSrc} alt="" aria-hidden="true" />
            <span>{tab.label}</span>
          </a>;
        })}
      </div> : null}
    </div> : <a key={item.id} href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
      <WorkspaceNavigationIcon name={item.id} />
      <span>{item.label}</span>
    </a>)}
  </nav>;
}
