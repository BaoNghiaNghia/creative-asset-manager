import { InventoryDailyTab } from "../ai-operations/InventoryDailyTab";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";

/** Primary Inventory destination. Keep /inventory/* legacy detail routes intact. */
export function InventoryOverviewPage() {
  return <main className="inventory-workspace">
    <WorkspacePageHeader route="inventory" actions={<WorkspaceBackToAssets />} />
    <section className="inventory-workspace-content" aria-label="Inventory Daily">
      <InventoryDailyTab />
    </section>
  </main>;
}
