import { useEffect, useState, type ReactNode } from "react";
import { InventoryDailyTab } from "../ai-operations/InventoryDailyTab";
import { InventoryDailyPipeline } from "./InventoryDailyPipeline";
import { InventoryCandidateReviewPanel } from "./InventoryCandidateReviewPanel";
import { InventoryCatinatShiftReadiness } from "./InventoryCatinatShiftReadiness";
import { InventoryCatinatApprovals } from "./InventoryCatinatApprovals";
import "./InventoryCompact.css";

/** Mount infrequently used workspaces only when opened, avoiding hidden API requests. */
function InventoryDeferredSection({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <details className="inventory-extra-section" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{title}</summary>
    {open ? <div className="inventory-extra-section-content">{children}</div> : null}
  </details>;
}

/** The only Inventory surface: preserve old bookmarks by collapsing to /inventory. */
export function InventoryOverviewPage() {
  useEffect(() => {
    if (window.location.pathname.replace(/\/$/, "") !== "/inventory") {
      window.history.replaceState(window.history.state, "", "/inventory");
    }
  }, []);
  return <main className="inventory-workspace">
    <section className="inventory-workspace-content" aria-label="Inventory Daily">
      <InventoryDailyTab />
      <InventoryDailyPipeline embedded />
      <section className="inventory-extra-workflows" aria-label="Các công việc chuyên sâu">
        <InventoryDeferredSection title="Duyệt vật tư và đối chiếu bằng chứng"><InventoryCandidateReviewPanel /></InventoryDeferredSection>
        <InventoryDeferredSection title="Kiểm đếm ca tối · chứng cứ Google Sheets"><InventoryCatinatShiftReadiness /></InventoryDeferredSection>
        <InventoryDeferredSection title="Duyệt ngoại lệ Catinat"><InventoryCatinatApprovals /></InventoryDeferredSection>
      </section>
    </section>
  </main>;
}
