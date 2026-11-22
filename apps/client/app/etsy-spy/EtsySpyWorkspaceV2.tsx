import { useEffect, useState } from "react";
import type { Listing } from "./api";

const prettify = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString("vi-VN");

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
  const mainImage = safeSpyPreview(listing.listingImages?.find(group => group.mainThumbnailUrl)?.mainThumbnailUrl)
    ?? safeSpyPreview(listing.fullImageUrl ?? listing.thumbnailUrl);
  const originalImage = safeSpyPreview(listing.thumbnailUrl ?? listing.metadata?.thumbnailUrl);
  const designImage = safeSpyPreview(listing.designs?.find(design => design.thumbnail_url)?.thumbnail_url);
  const imageChoices = [
    {src: mainImage, label: "Ảnh listing"},
    {src: originalImage, label: "Ảnh Etsy"},
    {src: designImage, label: "Design"},
  ].filter((image, index, all): image is {src:string;label:string} =>
    Boolean(image.src) && all.findIndex(candidate => candidate.src === image.src) === index);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setActiveImageIndex(0);
    setCopied(false);
  }, [listing.listingKey]);
  const activeImage = imageChoices[activeImageIndex] ?? imageChoices[0];
  const seoTerms = listing.tiktokListing?.result?.search_terms ??
    listing.tiktokListing?.search_terms_preview ?? [];
  const smsTerms = listing.keywordSms?.length
    ? listing.keywordSms
    : listing.metadata?.keywordSms ?? [];
  const imageCount = listing.listingImages?.reduce((total, group) => total + group.count, 0) ?? 0;
  // Retain the API-provided total rather than inferring that a saved video passed QA.
  const videoCount = listing.videoUgc?.count ?? 0;
  const badge = listing.badge || listing.searchBadge;
  const badgeKind = badge?.trim().toLowerCase() === "bestseller" ? "bestseller" :
    badge?.trim().toLowerCase() === "popular now" ? "popular" : "neutral";
  const copyTitle = async () => {
    if (!navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(listing.pageTitle);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return <aside className="etsy-v2-inspector" aria-label="Quick Inspect sản phẩm">
    <header className="etsy-v2-inspector-header">
      <div>
        <strong>Quick Inspect</strong>
        <small>Listing #{listing.listingKey}</small>
      </div>
      <button type="button" onClick={onClose} aria-label="Đóng Quick Inspect" title="Đóng Quick Inspect">×</button>
    </header>
    <div className="etsy-v2-inspector-scroll">
      <div className="etsy-v2-inspector-visual">
        <div className="etsy-v2-inspector-hero">
          {activeImage ? <img src={activeImage.src} alt={activeImage.label + " · " + listing.pageTitle}
            loading="lazy" decoding="async" /> :
            <div className="etsy-v2-no-preview">Chưa có ảnh preview</div>}
          {activeImage && <span className="etsy-v2-inspector-hero-label">{activeImage.label}</span>}
        </div>
        <div className="etsy-v2-inspector-thumbs" role="group" aria-label="Chọn ảnh xem nhanh">
          {imageChoices.map((choice, index) => <button key={choice.src} type="button"
            className={index === (activeImageIndex < imageChoices.length ? activeImageIndex : 0)
              ? "is-active" : ""}
            aria-label={"Xem " + choice.label} aria-pressed={choice.src === activeImage?.src}
            onClick={() => setActiveImageIndex(index)} title={choice.label}>
            <img src={choice.src} loading="lazy" decoding="async" alt="" />
          </button>)}
          {imageCount > 0 && <span
            title={imageCount + " ảnh Listing Image đã lưu · Xem đầy đủ qua cột Media"}>
            {imageCount} ảnh
          </span>}
        </div>
      </div>
      <div className="etsy-v2-inspector-identity">
        <strong className="etsy-v2-inspector-title" title={listing.pageTitle}>{listing.pageTitle}</strong>
        {badge && <span className={"etsy-v2-inspector-badge is-" + badgeKind}
          title={"Etsy: " + badge} aria-label={"Etsy: " + badge}>{badge}</span>}
      </div>
      <div className="etsy-v2-inspector-metrics" aria-label="Chỉ số listing">
        <div><small>Views / 24h</small><strong>{prettify(listing.views24h)}</strong></div>
        <div><small>Carts</small><strong>{listing.inCarts === 20 ? "20+" : prettify(listing.inCarts)}</strong></div>
        <div><small>Bought / 24h</small><strong>{prettify(listing.bought24h)}</strong></div>
      </div>
      <section className="etsy-v2-inspector-section etsy-v2-inspector-assets">
        <h3>Creative assets</h3>
        <div className="etsy-v2-inspector-medias">
          <span><span>Listing Image</span><strong>{imageCount} <small>ảnh</small></strong></span>
          <span><span>Video UGC</span><strong>{videoCount} <small>video</small></strong></span>
        </div>
      </section>
      <div className="etsy-v2-inspector-keywords">
        <section className="etsy-v2-inspector-section">
          <h3>TikTok SEO</h3>
          {seoTerms.length ? <div className="etsy-v2-inspector-tags">
            {seoTerms.slice(0, 8).map((term, index) => <span key={term + index}>{term}</span>)}
          </div> : <p>{listing.tiktokListing?.status || "Chưa có bản SEO"}</p>}
        </section>
        <section className="etsy-v2-inspector-section">
          <h3>Keyword SMS</h3>
          {smsTerms.length ? <div className="etsy-v2-inspector-tags">
            {smsTerms.slice(0, 5).map((term, index) => <span key={term + index}>{term}</span>)}
          </div> : <p>Chưa có keyword đồng bộ</p>}
        </section>
      </div>
    </div>
    <footer className="etsy-v2-inspector-footer">
      <div className="etsy-v2-inspector-actions">
        {listing.url ? <a href={listing.url} target="_blank" rel="noopener noreferrer">
          Mở trên Etsy ↗</a> : <span className="etsy-v2-inspector-action-empty">Không có link Etsy</span>}
        <button type="button" onClick={() => void copyTitle()}
          aria-label="Sao chép tiêu đề listing">{copied ? "Đã copy ✓" : "Copy title"}</button>
        <button type="button" disabled={!canManage || busy} onClick={() => onPicked(listing)}>
          {listing.picked ? "Bỏ Pick" : "Pick sản phẩm"}</button>
        <button type="button" disabled={!canManage || busy || listing.picked}
          onClick={() => onLater(listing)}>{listing.later ? "Bỏ Later" : "Later"}</button>
      </div>
    </footer>
  </aside>;
}
