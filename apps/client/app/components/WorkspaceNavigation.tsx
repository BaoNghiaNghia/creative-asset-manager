import { useEffect, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import accessManagementIcon from "../../assets/navigation/access-management.svg";
import aiOperationsIcon from "../../assets/navigation/ai-operations.svg";
import assetExplorerIcon from "../../assets/navigation/asset-explorer.svg";
import jobQueueIcon from "../../assets/navigation/job-queue.svg";
import reviewBoardIcon from "../../assets/navigation/review-board.svg";
import videoGenerationIcon from "../../assets/navigation/video-generation.svg";

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
}: {
  active: WorkspaceRoute;
  showOperations?: boolean;
  showReviewBoard?: boolean;
}) {
  const [visible, setVisible] = useState(showReviewBoard ?? false);

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
    {items.map(item => <a key={item.id} href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
      <WorkspaceNavigationIcon name={item.id} />
      <span>{item.label}</span>
    </a>)}
  </nav>;
}
