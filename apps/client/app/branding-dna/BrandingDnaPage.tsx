import { useState } from "react";
import "./BrandingDnaPage.css";
import "./BrandingDnaWorkspace.css";
import "./BrandingDnaOpsStyle.css";
import "./BrandingDnaStageTabs.css";
import "./BrandingDnaUnified.css";
import "./BrandingDnaFullWidth.css";
import { BrandingDnaFoundation } from "./BrandingDnaFoundation";
import { BrandingDnaPolicyReviews } from "./BrandingDnaPolicyReviews";
import { BrandingDnaWorkers } from "./BrandingDnaWorkers";
import { BrandingDnaScanReadiness } from "./BrandingDnaScanReadiness";
import { BrandingDnaEvidence } from "./BrandingDnaEvidence";
import { BrandingDnaKnowledge } from "./BrandingDnaKnowledge";
import { BrandingDnaReadiness } from "./BrandingDnaReadiness";
import { WorkspacePageHeader, WorkspaceBackToAssets } from "../components/WorkspacePageHeader";

const DEMO_TABS = ["overview", "shops", "scanner", "products", "knowledge", "dna"] as const;
type DemoTab = typeof DEMO_TABS[number];

export function brandingDnaTabForPath(path: string): DemoTab {
  const segment = path.replace(/\/$/, "").split("/").at(-1) || "";
  return DEMO_TABS.includes(segment as DemoTab) ? segment as DemoTab : "overview";
}

/**
 * The separate visual prototype is isolated from real CAM data. Shop registry,
 * worker control and metadata-only Product Explorer call tenant-scoped APIs.
 * No production Browser Scanner or Brand DNA inference runs from this page.
 */
export function BrandingDnaPage() {
  const tab = brandingDnaTabForPath(typeof window === "undefined" ? "/branding-dna" : window.location.pathname);
  const [activeTab, setActiveTab] = useState<DemoTab>(tab);
  const tabs: { id: DemoTab; title: string; note: string }[] = [
    { id: "overview", title: "Overview", note: "Tổng quan" },
    { id: "shops", title: "Shop Library", note: "Shop thật" },
    { id: "scanner", title: "Browser Scanner", note: "Chưa quét" },
    { id: "products", title: "Product Explorer", note: "Import evidence" },
    { id: "knowledge", title: "Shop Knowledge", note: "RAG Library" },
    { id: "dna", title: "Brand DNA", note: "Input Readiness" },
  ];
  return <main className="branding-dna-page">
    <WorkspacePageHeader route="branding-dna" className="ops-header bdna-ops-header"
      actions={<><span className="bdna-header-runtime">Scanner · Dispatch disabled</span><WorkspaceBackToAssets /></>} />
    <div className="bdna-stage-shell">
      <nav className="ops-tabs bdna-stage-tabs" aria-label="Branding DNA stages" role="tablist" onKeyDown={event => {
        const index = tabs.findIndex(item => item.id === activeTab);
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
        if (next < 0) return;
        event.preventDefault();
        setActiveTab(tabs[next].id);
        window.requestAnimationFrame(() => document.getElementById("bdna-stage-" + tabs[next].id)?.focus());
      }}>
        {tabs.map((item, index) => <button key={item.id} id={"bdna-stage-" + item.id} type="button" role="tab" aria-selected={activeTab === item.id} aria-controls={"bdna-panel-" + item.id} tabIndex={activeTab === item.id ? 0 : -1} className={activeTab === item.id ? "active" : ""} onClick={() => setActiveTab(item.id)}>
          <span className="bdna-stage-number" aria-hidden="true">{index}</span>
          <span className="bdna-stage-copy"><strong>{item.title}</strong><small>{item.note}</small></span>
        </button>)}
      </nav>
    </div>
    <section id={"bdna-panel-" + activeTab} role="tabpanel" aria-labelledby={"bdna-stage-" + activeTab} className="bdna-stage-panel">
    <div className="bdna-workspace-heading">
      <div><span>BRANDING DNA / STAGE {tabs.findIndex(item => item.id === activeTab)}</span><h2>{tabs.find(item => item.id === activeTab)?.title}</h2>
        <p>{activeTab === "overview" ? "Production foundation, worker status và lộ trình triển khai." : activeTab === "shops" ? "Quản lý các shop được lưu theo tenant bằng dữ liệu thật." : activeTab === "scanner" ? "Lớp điều phối trình duyệt sẽ chỉ hoạt động sau khi worker và evidence pipeline đạt yêu cầu." : activeTab === "products" ? "Hiển thị listing/gallery được nhập từ nguồn có quyền, đánh dấu unverified cho tới khi đối chiếu." : activeTab === "knowledge" ? "Upload tài liệu, duyệt nguồn và tìm lại nội dung RAG cho từng shop." : "Brand identity và insights sẽ hình thành từ evidence sản phẩm đã được kiểm chứng."}</p></div>
      <span className="bdna-workspace-state">{activeTab === "overview" || activeTab === "shops" ? "LIVE REGISTRY" : activeTab === "products" ? "METADATA · UNVERIFIED" : activeTab === "knowledge" ? "RAG LIBRARY · REVIEW" : "INPUT GATES · READ ONLY"}</span>
    </div>
    {activeTab === "overview" && <div className="bdna-workflow-overview" aria-label="Branding DNA workflow">
      <div><span>01</span><strong>Register shops</strong><small>Shop Registry · dữ liệu tenant thật</small></div>
      <div><span>02</span><strong>Connect browser worker</strong><small>Dedicated Windows Agent</small></div>
      <div><span>03</span><strong>Collect product evidence</strong><small>Authorized metadata import · scanner pending</small></div>
      <div><span>04</span><strong>Build Brand DNA</strong><small>Phân tích dựa trên evidence</small></div>
    </div>}
    {(activeTab === "overview" || activeTab === "shops") && <BrandingDnaFoundation compact={activeTab === "overview"} onOpenShops={() => setActiveTab("shops")} />}
    {activeTab === "shops" && <BrandingDnaPolicyReviews />}
    {(activeTab === "overview" || activeTab === "scanner") && <BrandingDnaWorkers compact={activeTab === "overview"} onOpenScanner={() => setActiveTab("scanner")} />}
    {activeTab === "scanner" && <BrandingDnaScanReadiness />}
    {activeTab === "products" && <BrandingDnaEvidence />}
    {activeTab === "knowledge" && <BrandingDnaKnowledge />}
    {activeTab === "dna" && <BrandingDnaReadiness onOpenKnowledge={() => setActiveTab("knowledge")} onOpenShops={() => setActiveTab("shops")} />}
    </section>
    <details className="branding-dna-preview-link" aria-label="Branding DNA design preview"><summary>Giao diện prototype · dữ liệu mô phỏng <span>Chi tiết</span></summary><div><p>Shop Registry, Worker Registry, Product Explorer và Shop Knowledge sử dụng API dữ liệu tenant thực. Product Explorer chỉ hiển thị bản export chưa xác minh; Browser Scanner và phân tích Brand DNA vẫn chưa hoạt động. Prototype riêng chỉ dùng dữ liệu mô phỏng.</p><a href={`/branding-dna-preview/index.html?view=${encodeURIComponent(activeTab)}`} target="_blank" rel="noopener noreferrer">Xem prototype ↗</a></div></details>
  </main>;
}
