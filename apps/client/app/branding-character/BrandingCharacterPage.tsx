import "./BrandingCharacterPage.css";

const DEMO_TABS = ["overview", "shops", "scanner", "products", "dna"] as const;
type DemoTab = typeof DEMO_TABS[number];

export function brandingCharacterTabForPath(path: string): DemoTab {
  const segment = path.replace(/\/$/, "").split("/").at(-1) || "";
  return DEMO_TABS.includes(segment as DemoTab) ? segment as DemoTab : "overview";
}

/**
 * The browser prototype is intentionally isolated from CAM credentials and data.
 * It is not a browser scanner; action buttons only update in-frame sample state.
 * Integrate data via real tenant-scoped APIs only after the scanner is audited.
 */
export function BrandingCharacterPage() {
  const tab = brandingCharacterTabForPath(typeof window === "undefined" ? "/branding-character" : window.location.pathname);
  return <main className="branding-character-page">
    <header className="branding-character-heading">
      <div className="branding-character-heading-copy">
        <span className="branding-character-eyebrow">SHOP INTELLIGENCE · DESIGN PREVIEW</span>
        <h1>Branding Character</h1>
        <p>Shop Library · Browser Scanner · Product Explorer · Brand DNA</p>
      </div>
      <span className="branding-character-demo-label" role="status">Prototype · dữ liệu mô phỏng</span>
    </header>
    <div className="branding-character-preview-notice">
      Bản UI để duyệt thiết kế. Các nút Scan, Add Shop, Approve và Export đang chạy thử trên dữ liệu mẫu;
      chưa kết nối tài khoản shop, Gemini hoặc database thật.
    </div>
    <iframe className="branding-character-preview-frame"
      title="Branding Character interactive design preview"
      src={`/branding-character/index.html?embed=1&view=${encodeURIComponent(tab)}`}
      sandbox="allow-scripts allow-downloads"
      referrerPolicy="no-referrer"
    />
  </main>;
}
