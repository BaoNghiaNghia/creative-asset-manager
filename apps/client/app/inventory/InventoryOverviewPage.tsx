import { useEffect } from "react";
import { InventoryDailyTab } from "../ai-operations/InventoryDailyTab";
import { InventoryDailyPipeline } from "./InventoryDailyPipeline";
import { InventoryCandidateReviewPanel } from "./InventoryCandidateReviewPanel";
import { InventoryCatinatShiftReadiness } from "./InventoryCatinatShiftReadiness";
import { InventoryCatinatApprovals } from "./InventoryCatinatApprovals";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";

/** The only Inventory surface: preserve old bookmarks by collapsing to /inventory. */
export function InventoryOverviewPage() {
  useEffect(() => {
    if (window.location.pathname.replace(/\/$/, "") !== "/inventory") {
      window.history.replaceState(window.history.state, "", "/inventory");
    }
  }, []);
  return <main className="inventory-workspace">
    <WorkspacePageHeader route="inventory" actions={<WorkspaceBackToAssets />} />
    <section className="inventory-workspace-content" aria-label="Inventory Daily">
      <InventoryDailyTab />
      <InventoryDailyPipeline embedded />
      <InventoryCandidateReviewPanel />
      <details className="inventory-pipeline"><summary><strong>Kiểm đếm ca tối và chứng cứ Google Sheets</strong></summary><InventoryCatinatShiftReadiness /></details>
      <details className="inventory-pipeline"><summary><strong>Duyệt ngoại lệ Catinat</strong></summary><InventoryCatinatApprovals /></details>
    </section>
  </main>;
}
