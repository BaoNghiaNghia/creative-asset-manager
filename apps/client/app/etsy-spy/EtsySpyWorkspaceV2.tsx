import type { Listing } from "./api";

export type SpyDensity = "compact" | "default" | "comfortable";
export type SpyScope = "eligible" | "picked" | "later";
export type SpyColumn = { id: string; text: string };

const prettify = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString("vi-VN");

export const OPTIONAL_SPY_COLUMNS = new Set([
  "design", "tiktokListing", "listingImage", "videoUgc", "keywordSms", "badge",
  "bought24h", "views24h", "inCarts", "observedAt",
]);

export function safeSpyPreview(url: string | null | undefined): string | null {
  if (!url || typeof url !== "string" || url.length > 1600) return null;
  if (url.startsWith("/") && !url.startsWith("//") && !url.includes("\\")) return url;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port) return null;
    const trusted = ["i.etsystatic.com", "img.etsystatic.com",
      "cam-r2-original-video.baonghia-kht.workers.dev"].includes(parsed.hostname);
    const sameOrigin = typeof window !== "undefined" && parsed.origin === window.location.origin;
    return trusted || sameOrigin ? parsed.href : null;
  } catch {
    return null;
  }
}

export function EtsySpyResultsToolbar({
  scope, counts, onScope, columns, hiddenColumns, onToggleColumn,
  density, onDensity, onToggleFilters, filtersExpanded, activeFilters,
  onClearFilters, resultCount, inspectorOpen,
}: {
  scope: SpyScope;
  counts: { eligible: number; picked: number; later: number };
  onScope: (value: SpyScope) => void;
  columns: readonly SpyColumn[];
  hiddenColumns: readonly string[];
  onToggleColumn: (key: string) => void;
  density: SpyDensity;
  onDensity: (value: SpyDensity) => void;
  onToggleFilters: () => void;
  filtersExpanded: boolean;
  activeFilters: readonly string[];
  onClearFilters: () => void;
  resultCount: number | null;
  inspectorOpen: boolean;
}) {
  const scopes = [
    { key: "eligible", name: "Eligible" },
    { key: "picked", name: "Picked" },
    { key: "later", name: "Later" },
  ] as const;
  return <div className="etsy-v2-results-toolbar" aria-label="Product view controls">
    <div className="etsy-v2-scope-tabs" role="group" aria-label="Trạng thái sản phẩm">
      {scopes.map(item => <button type="button" key={item.key}
        className={scope === item.key ? "active" : ""}
        aria-pressed={scope === item.key}
        onClick={() => onScope(item.key)}>
        {item.name} <span>{prettify(counts[item.key])}</span>
      </button>)}
    </div>
    <div className="etsy-v2-toolbar-actions">
      <span className="etsy-v2-result-count">{prettify(resultCount)} kết quả</span>
      <button type="button" className={"etsy-v2-control"+(filtersExpanded?" active":"")}
        aria-controls="etsy-facet-details" aria-expanded={filtersExpanded}
        onClick={onToggleFilters}>
        <span aria-hidden="true">☷</span> Bộ lọc{activeFilters.length ? " · "+activeFilters.length : ""}
      </button>
      <details className="etsy-v2-column-menu">
        <summary className="etsy-v2-control" aria-label="Tùy chỉnh các cột đang hiển thị">
          <span aria-hidden="true">▦</span> Cột
        </summary>
        <div className="etsy-v2-column-popover">
          <strong>Hiển thị cột</strong>
          <small>Product và Action luôn hiển thị.</small>
          {columns.filter(col => OPTIONAL_SPY_COLUMNS.has(col.id)).map(col =>
            <label key={col.id}>
              <input type="checkbox" checked={!hiddenColumns.includes(col.id)}
                onChange={() => onToggleColumn(col.id)} />
              <span>{col.text === "Media" ? "Listing Image" : col.text}</span>
            </label>)}
        </div>
      </details>
      <div className="etsy-v2-density" role="group" aria-label="Mật độ hiển thị">
        {([
          { key: "compact", label: "Gọn", symbol: "≡" },
          { key: "default", label: "Vừa", symbol: "☷" },
          { key: "comfortable", label: "Rộng", symbol: "☰" },
        ] as const).map(item =>
          <button type="button" key={item.key} title={item.label}
            aria-label={"Mật độ " + item.label}
            aria-pressed={density === item.key}
            className={density === item.key ? "active" : ""}
            onClick={() => onDensity(item.key)}>{item.symbol}</button>)}
      </div>
    </div>
    {activeFilters.length > 0 && <div className="etsy-v2-active-filters">
      {activeFilters.slice(0, 4).map(item => <span key={item}>{item}</span>)}
      {activeFilters.length > 4 && <span>+{activeFilters.length - 4}</span>}
      <button type="button" onClick={onClearFilters}>Xóa lọc</button>
    </div>}
    {inspectorOpen && <span className="etsy-v2-inspect-hint">Đang xem chi tiết sản phẩm</span>}
  </div>;
}

export function EtsySpyQuickInspect({
  listing, canManage, busy, onClose, onPicked, onLater,
}: {
  listing: Listing;
  canManage: boolean;
  busy: boolean;
  onClose: () => void;
  onPicked: (listing: Listing) => void;
  onLater: (listing: Listing) => void;
}) {
  const image = safeSpyPreview(listing.listingImages?.find(badge => badge.mainThumbnailUrl)?.mainThumbnailUrl)
    ?? safeSpyPreview(listing.fullImageUrl ?? listing.thumbnailUrl);
  const original = safeSpyPreview(listing.thumbnailUrl ?? listing.metadata?.thumbnailUrl);
  const designImage = safeSpyPreview(listing.designs?.find(design => design.thumbnail_url)?.thumbnail_url);
  const seoTerms = listing.tiktokListing?.result?.search_terms ??
    listing.tiktokListing?.search_terms_preview ?? [];
  const smsTerms = listing.keywordSms?.length
    ? listing.keywordSms
    : listing.metadata?.keywordSms ?? [];
  const imageCount = listing.listingImages?.reduce((total, group) => total + group.count, 0) ?? 0;
  const videoCount = listing.videoUgc?.count ?? 0;
  return <aside className="etsy-v2-inspector" aria-label="Quick Inspect sản phẩm">
    <header className="etsy-v2-inspector-header">
      <div><strong>Quick Inspect</strong><small>Thông tin listing đang chọn</small></div>
      <button type="button" onClick={onClose} aria-label="Đóng Quick Inspect">×</button>
    </header>
    <div className="etsy-v2-inspector-scroll">
      <div className="etsy-v2-inspector-hero">
        {image ? <img src={image} alt={"Ảnh listing " + listing.pageTitle}
          loading="lazy" decoding="async" /> : <div className="etsy-v2-no-preview">Chưa có ảnh preview</div>}
      </div>
      <div className="etsy-v2-inspector-thumbs">
        {[original, designImage, image].filter((src): src is string => !!src)
          .filter((src, i, items) => items.indexOf(src) === i).map((src, index) =>
            <img key={src} src={src} loading="lazy" alt={"Ảnh xem nhanh " + (index + 1)} />)}
        {imageCount > 3 && <span>+{imageCount - 3}</span>}
      </div>
      <strong className="etsy-v2-inspector-title">{listing.pageTitle}</strong>
      <div className="etsy-v2-inspector-id">Listing #{listing.listingKey}</div>
      <div className="etsy-v2-inspector-metrics">
        <div><small>Views / 24h</small><strong>{prettify(listing.views24h)}</strong></div>
        <div><small>Carts</small><strong>{listing.inCarts === 20 ? "20+" : prettify(listing.inCarts)}</strong></div>
        <div><small>Bought / 24h</small><strong>{prettify(listing.bought24h)}</strong></div>
      </div>
      <section className="etsy-v2-inspector-section">
        <h3>Media</h3>
        <div className="etsy-v2-inspector-medias">
          <span>Listing Image <strong>{imageCount}</strong></span>
          <span>Video UGC <strong>{videoCount}</strong></span>
        </div>
      </section>
      <section className="etsy-v2-inspector-section">
        <h3>TikTok SEO</h3>
        {seoTerms.length ? <div className="etsy-v2-inspector-tags">
          {seoTerms.slice(0, 8).map((term, i) => <span key={term+i}>{term}</span>)}
        </div> : <p>{listing.tiktokListing?.status || "Chưa có bản SEO"}</p>}
      </section>
      <section className="etsy-v2-inspector-section">
        <h3>Keyword SMS</h3>
        {smsTerms.length ? <div className="etsy-v2-inspector-tags">
          {smsTerms.slice(0, 5).map((term, i) => <span key={term+i}>{term}</span>)}
        </div> : <p>Chưa có keyword đồng bộ</p>}
      </section>
      {listing.badge && <span className="etsy-v2-inspector-badge">{listing.badge}</span>}
      <div className="etsy-v2-inspector-actions">
        {listing.url && <a href={listing.url} target="_blank" rel="noopener noreferrer">
          Mở trên Etsy ↗</a>}
        <button type="button" onClick={() => void navigator.clipboard?.writeText(listing.pageTitle)}>
          Copy title</button>
        <button type="button" disabled={!canManage || busy} onClick={() => onPicked(listing)}>
          {listing.picked ? "Bỏ Pick" : "Pick sản phẩm"}</button>
        <button type="button" disabled={!canManage || busy || listing.picked}
          onClick={() => onLater(listing)}>{listing.later ? "Bỏ Later" : "Later"}</button>
      </div>
    </div>
  </aside>;
}
