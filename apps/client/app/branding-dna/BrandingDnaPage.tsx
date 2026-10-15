import { useState } from "react";
import "./BrandingDnaPage.css";
import "./BrandingDnaWorkspace.css";
import { BrandingDnaFoundation } from "./BrandingDnaFoundation";
import { BrandingDnaWorkers } from "./BrandingDnaWorkers";

const DEMO_TABS = ["overview", "shops", "scanner", "products", "dna"] as const;
type DemoTab = typeof DEMO_TABS[number];

export function brandingDnaTabForPath(path: string): DemoTab {
  const segment = path.replace(/\/$/, "").split("/").at(-1) || "";
  return DEMO_TABS.includes(segment as DemoTab) ? segment as DemoTab : "overview";
}

/**
 * The browser prototype is intentionally isolated from CAM credentials and data.
 * It is not a browser scanner; action buttons only update in-frame sample state.
 * Integrate data via real tenant-scoped APIs only after the scanner is audited.
 */
export function BrandingDnaPage() {
  const tab = brandingDnaTabForPath(typeof window === "undefined" ? "/branding-dna" : window.location.pathname);
  const [activeTab, setActiveTab] = useState<DemoTab>(tab);
  const tabs: { id: DemoTab; title: string; note: string }[] = [
    { id: "overview", title: "Overview", note: "Tổng quan" },
    { id: "shops", title: "Shop Library", note: "Shop thật" },
    { id: "scanner", title: "Browser Scanner", note: "Chờ kết nối" },
    { id: "products", title: "Product Explorer", note: "Chờ evidence" },
    { id: "dna", title: "Brand DNA", note: "Chờ phân tích" },
  ];
  return <main className="branding-dna-page">
    <header className="branding-dna-heading">
      <div className="branding-dna-heading-copy">
        <span className="branding-dna-eyebrow">SHOP INTELLIGENCE · DESIGN PREVIEW</span>
        <h1>Branding DNA</h1>
        <p>Shop Library · Browser Scanner · Product Explorer · Brand DNA</p>
      </div>
      <span className="branding-dna-demo-label" role="status">Worker control plane · Scanner disabled</span>
    </header>
    <div className="branding-dna-preview-notice">
      Shop Registry và quản lý Worker kết nối PostgreSQL. Phía dưới: UI prototype dùng dữ liệu mô phỏng; các nút Scan, Add Shop, Approve, Export chưa kết nối dữ liệu thật;
      chưa kết nối Browser Scanner, Gemini hoặc nguồn evidence thật.
    </div>
    <nav className="bdna-navigation" aria-label="Branding DNA workspace">
      {tabs.map(item => <button key={item.id} type="button" onClick={() => setActiveTab(item.id)} aria-current={activeTab === item.id ? "page" : undefined}>
        <strong>{item.title}</strong><small>{item.note}</small>
      </button>)}
    </nav>
    <div className="bdna-workspace-heading">
      <div><span>WORKSPACE / {activeTab.toUpperCase()}</span><h2>{tabs.find(item => item.id === activeTab)?.title}</h2>
        <p>{activeTab === "overview" ? "Production foundation, worker status và lộ trình triển khai." : activeTab === "shops" ? "Quản lý các shop được lưu theo tenant bằng dữ liệu thật." : activeTab === "scanner" ? "Lớp điều phối trình duyệt sẽ chỉ hoạt động sau khi worker và evidence pipeline đạt yêu cầu." : activeTab === "products" ? "Sản phẩm sẽ hiển thị tại đây khi đã có dữ liệu quét có thể kiểm chứng." : "Brand identity và insights sẽ hình thành từ evidence sản phẩm đã được kiểm chứng."}</p></div>
      <span className="bdna-workspace-state">{activeTab === "overview" || activeTab === "shops" ? "LIVE REGISTRY" : "NOT CONNECTED"}</span>
    </div>
    {(activeTab === "overview" || activeTab === "shops") && <BrandingDnaFoundation />}
    {(activeTab === "overview" || activeTab === "scanner") && <BrandingDnaWorkers />}
    {(activeTab === "products" || activeTab === "dna") && <section className="bdna-future-panel">
      <div className="bdna-future-illustration" aria-hidden="true"><span>01</span><span>02</span><span>03</span></div>
      <strong>{activeTab === "products" ? "Product Explorer · Awaiting scanned products" : "Brand DNA · Awaiting verified evidence"}</strong>
      <p>{activeTab === "products" ? "Dữ liệu sản phẩm sẽ có ảnh, URL gốc, biến thể, giá và lịch sử scan; không tạo sản phẩm giả trong production." : "Tổng hợp style, palette, typography, positioning và creative patterns dựa trên các nguồn được trích dẫn."}</p>
      <div className="bdna-future-steps"><span>1 · Add Shop</span><span>2 · Connect Worker</span><span>3 · Verify Evidence</span><span>4 · Generate Insights</span></div>
      <button type="button" onClick={() => setActiveTab("shops")}>Đi đến Shop Library →</button>
    </section>}
    <section className="branding-dna-preview-link" aria-label="Branding DNA design preview">
      <div>
        <strong>UI Prototype · dữ liệu mô phỏng</strong>
        <p>Khám phá giao diện Browser Scanner, Product Explorer và Brand DNA bằng dữ liệu mẫu. Trang này mở riêng để giữ nguyên chính sách bảo mật của CAM.</p>
      </div>
      <a href={`/branding-dna-preview/index.html?view=${encodeURIComponent(activeTab)}`} target="_blank" rel="noopener noreferrer">Mở giao diện mẫu ↗</a>
    </section>
  </main>;
}
