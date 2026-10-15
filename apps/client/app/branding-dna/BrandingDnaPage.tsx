import "./BrandingDnaPage.css";

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
      <span className="branding-dna-demo-label" role="status">Prototype · dữ liệu mô phỏng</span>
    </header>
    <div className="branding-dna-preview-notice">
      Bản UI để duyệt thiết kế. Các nút Scan, Add Shop, Approve và Export đang chạy thử trên dữ liệu mẫu;
      chưa kết nối tài khoản shop, Gemini hoặc database thật.
    </div>
    <iframe className="branding-dna-preview-frame"
      title="Branding DNA interactive design preview"
      src={`/branding-dna/index.html?embed=1&view=${encodeURIComponent(tab)}`}
      sandbox="allow-scripts allow-downloads"
      referrerPolicy="no-referrer"
    />
  </main>;
}
