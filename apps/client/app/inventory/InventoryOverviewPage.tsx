import { useEffect } from "react";
import { InventoryDailyTab } from "../ai-operations/InventoryDailyTab";
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
    </section>
  </main>;
}
