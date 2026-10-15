import { useEffect, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import accessManagementIcon from "../../assets/navigation/access-management.svg";
import aiOperationsIcon from "../../assets/navigation/ai-operations.svg";
import brandingDnaIcon from "../../assets/navigation/branding-dna.svg";
import inventoryIcon from "../../assets/navigation/inventory-daily.svg";
import assetExplorerIcon from "../../assets/navigation/asset-explorer.svg";
import jobQueueIcon from "../../assets/navigation/job-queue.svg";
import reviewBoardIcon from "../../assets/navigation/review-board.svg";
import realisticReviewUgcIcon from "../../assets/navigation/realistic-review-ugc.svg";
import videoGenerationIcon from "../../assets/navigation/video-generation.svg";
import { currentRrugcStage, rrugcWorkflowHref, workflowGroupForStage, RRUGC_WORKFLOW_GROUPS, type RrugcStageTab } from "../realistic-review-ugc/workflowRouting";
import {
  AI_OPERATIONS_TABS,
  aiOperationsTabHref,
  type AiOpsTab,
} from "../ai-operations/navigation";

const INVENTORY_SECTIONS = [
  { href: "/inventory/pipeline", label: "Tiến trình", icon: "pipeline" },
  { href: "/inventory/knowledge", label: "Kiến thức", icon: "knowledge" },
  { href: "/inventory/materials", label: "Nguyên vật liệu", icon: "materials" },
  { href: "/inventory/prompts", label: "Gemini Prompts", icon: "prompts" },
  { href: "/inventory/settings", label: "Cấu hình", icon: "settings" },
] as const;
type InventorySectionIcon = typeof INVENTORY_SECTIONS[number]["icon"];
export function InventoryNavIcon({ name }: { name: InventorySectionIcon }) {
  const paths: Record<InventorySectionIcon, string[]> = {
    pipeline: ["M3 12h4l3-7 4 14 3-7h4", "M4 4h4", "M16 20h4"],
    knowledge: ["M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Z", "M12 6v14", "M6 9h3", "M15 9h3"],
    materials: ["m12 3 9 5-9 5-9-5 9-5Z", "m3 12 9 5 9-5", "m3 16 9 5 9-5"],
    prompts: ["M5 5h14v11H9l-4 4V5Z", "m12 7 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z"],
    settings: ["M4 6h16", "M4 12h16", "M4 18h16", "M8 3v6", "M16 9v6", "M10 15v6"],
  };
  return <svg className="workspace-nav-submenu-icon inventory-section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name].map((d,i) => <path key={i} d={d}/>)}</svg>;
}

export type WorkspaceRoute = "assets" | "inventory" | "operations" | "branding-dna" | "queue" | "generation" | "realistic-review-ugc" | "review-board" | "access";

export const mayViewReviewBoard = (permissions: readonly string[]) =>
  permissions.includes("public_review.read")
  || permissions.includes("realistic_review_ugc.read");

const navigationIconSources: Record<WorkspaceRoute, string> = {
  assets: assetExplorerIcon,
  inventory: inventoryIcon,
  operations: aiOperationsIcon,
  "branding-dna": brandingDnaIcon,
  queue: jobQueueIcon,
  generation: videoGenerationIcon,
  "realistic-review-ugc": realisticReviewUgcIcon,
  "review-board": reviewBoardIcon,
  access: accessManagementIcon,
};

export function WorkspaceNavigationIcon({ name }: { name: WorkspaceRoute }) {
  return <img className="workspace-nav-icon" src={navigationIconSources[name]} alt="" aria-hidden="true" />;
}

export function WorkspaceNavigation({
  active,
  showOperations = true,
  showReviewBoard,
  permissions,
  aiOperationsTab,
  onAiOperationsTab,
  rrugcStage,
}: {
  active: WorkspaceRoute;
  showOperations?: boolean;
  showReviewBoard?: boolean;
  permissions?: readonly string[];
  aiOperationsTab?: AiOpsTab;
  onAiOperationsTab?: (tab: AiOpsTab) => void;
  rrugcStage?: RrugcStageTab;
}) {
  const [visible, setVisible] = useState(showReviewBoard ?? false);
  const [inventoryOpen, setInventoryOpen] = useState(active === "inventory");
  const [operationsOpen, setOperationsOpen] = useState(active === "operations");
  const [rrugcOpen, setRrugcOpen] = useState(active === "realistic-review-ugc");
  const selectedRrugcGroup = workflowGroupForStage(rrugcStage ?? currentRrugcStage());

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
    if (active === "inventory") setInventoryOpen(true);
    if (active === "operations") setOperationsOpen(true);
    if (active === "realistic-review-ugc") setRrugcOpen(true);
  }, [active]);

  const permissionAware = permissions !== undefined;
  const canViewOperations = showOperations
    && (!permissionAware || permissions.includes("ai_operations.read"));
  const canViewInventory = !permissionAware || permissions.includes("inventory.read");
  const canViewRrugc = !permissionAware || permissions.includes("realistic_review_ugc.read");
  const canViewAccess = !permissionAware || permissions.includes("tenant_members.read");

  const items: Array<{ id: WorkspaceRoute; href: string; label: string }> = [
    { id: "assets", href: "/", label: "Asset Explorer" },
    ...(canViewInventory ? [
      { id: "inventory" as const, href: "/inventory", label: "Inventory" },
    ] : []),
    ...(canViewOperations ? [
      { id: "operations" as const, href: "/ai-operations", label: "AI Operations" },
      { id: "branding-dna" as const, href: "/branding-dna", label: "Branding DNA" },
    ] : []),
    ...(canViewRrugc ? [
      { id: "realistic-review-ugc" as const, href: rrugcWorkflowHref("products"), label: "Realistic Review UGC" },
    ] : []),
    ...(canViewOperations ? [
      { id: "queue" as const, href: "/job-queue", label: "Job Queue" },
    ] : []),
    { id: "generation", href: "/video-generation", label: "Video Generation" },
    ...(visible ? [{ id: "review-board" as const, href: "/review-board", label: "Review Board" }] : []),
    ...(canViewAccess ? [
      { id: "access" as const, href: "/settings/access", label: "Access Management" },
    ] : []),
  ];

  return <nav className="workspace-navigation" aria-label="Workspace navigation">
    {items.map(item => item.id === "inventory" ? <div key={item.id} className={"workspace-nav-group" + (active === "inventory" ? " active" : "")}>
      <div className="workspace-nav-group-row">
        <a href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
          <WorkspaceNavigationIcon name={item.id} /><span>{item.label}</span>
        </a>
        <button type="button" className="workspace-nav-expander"
          aria-label={(inventoryOpen ? "Collapse" : "Expand") + " Inventory navigation"}
          aria-expanded={inventoryOpen} aria-controls="workspace-inventory-submenu"
          onClick={() => setInventoryOpen(value => !value)}>
          <svg className="workspace-nav-expander-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m5.5 7.5 4.5 4.5 4.5-4.5" /></svg>
        </button>
      </div>
      {inventoryOpen ? <div id="workspace-inventory-submenu" className="workspace-nav-submenu" aria-label="Inventory sections">
        {INVENTORY_SECTIONS.map(section => {
          const selected = active === "inventory" && typeof window !== "undefined" && window.location.pathname.replace(/\/$/, "") === section.href;
          return <a key={section.href} href={section.href} className={selected ? "active" : undefined} aria-current={selected ? "location" : undefined}>
            <InventoryNavIcon name={section.icon} />
            <span>{section.label}</span>
          </a>;
        })}
      </div> : null}
    </div> : item.id === "operations" ? <div key={item.id} className={"workspace-nav-group" + (active === "operations" ? " active" : "")}>
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
          <svg className="workspace-nav-expander-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="m5.5 7.5 4.5 4.5 4.5-4.5" />
          </svg>
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
    </div> : item.id === "realistic-review-ugc" ? <div key={item.id} className={"workspace-nav-group workspace-nav-group-rrugc" + (active === item.id ? " active" : "")}>
      <div className="workspace-nav-group-row">
        <a href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
          <WorkspaceNavigationIcon name={item.id} />
          <span>{item.label}</span>
        </a>
        <button
          type="button"
          className="workspace-nav-expander"
          aria-label={(rrugcOpen ? "Collapse" : "Expand") + " Realistic Review UGC navigation"}
          aria-expanded={rrugcOpen}
          aria-controls="workspace-rrugc-submenu"
          onClick={() => setRrugcOpen(value => !value)}
        >
          <svg className="workspace-nav-expander-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="m5.5 7.5 4.5 4.5 4.5-4.5" />
          </svg>
        </button>
      </div>
      {rrugcOpen ? <div id="workspace-rrugc-submenu" className="workspace-nav-submenu workspace-nav-rrugc-submenu" aria-label="GatorHats sections">
        {RRUGC_WORKFLOW_GROUPS.map(group => {
          const selected = active === "realistic-review-ugc" && selectedRrugcGroup === group.id;
          return <a key={group.id} href={rrugcWorkflowHref(group.id)}
            className={selected ? "active" : undefined}
            aria-current={selected ? "location" : undefined}
            data-rrugc-workflow={group.id}>
            <svg className="workspace-nav-submenu-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              {group.id === "products" ? <>
                <path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="M3 8v8l9 5 9-5V8M12 13v8" />
              </> : <path d="m12 3 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.8l-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9L12 3Z" />}
            </svg>
            <span>{group.title}</span>
          </a>;
        })}
      </div> : null}
    </div> : <a key={item.id} href={item.href} className={active === item.id ? "active" : undefined} aria-current={active === item.id ? "page" : undefined}>
      <WorkspaceNavigationIcon name={item.id} />
      <span>{item.label}</span>
    </a>)}
  </nav>;
}
