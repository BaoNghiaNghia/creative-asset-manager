import "./BrandingDnaPage.css";
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
    <BrandingDnaFoundation />
    <BrandingDnaWorkers />
    <section className="branding-dna-preview-link" aria-label="Branding DNA design preview">
      <div>
        <strong>UI Prototype · dữ liệu mô phỏng</strong>
        <p>Khám phá giao diện Browser Scanner, Product Explorer và Brand DNA bằng dữ liệu mẫu. Trang này mở riêng để giữ nguyên chính sách bảo mật của CAM.</p>
      </div>
      <a href={`/branding-dna-preview/index.html?view=${encodeURIComponent(tab)}`} target="_blank" rel="noopener noreferrer">Mở giao diện mẫu ↗</a>
    </section>
  </main>;
}
