import { memo, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { spyFetch, type Listing, type ListingImageGallery, type ListingImageOutput } from "./api";
import { EtsyDeferredImage } from "./EtsyDeferredImage";
import "./ListingImageCell.css";

const MODEL = /^(C1717|G500|G180|G185)$/;
const safeBadge = (code: string, count: number) =>
  MODEL.test(code) && Number.isSafeInteger(count) && count > 0 && count <= 1000;

type Props = { item: Listing; onPreviewStart?: () => void };

// A missing WebP conversion must not trigger an original 1254px+ PNG fetch.
// Use the already saved 192px thumbnail as the fail-safe visual fallback.
function ListingReviewPreview({ image }: { image: ListingImageOutput }) {
  const [fallback, setFallback] = useState(false);
  const initial = image.previewUrl || image.thumbnailUrl;
  const source = fallback ? image.thumbnailUrl : initial;
  return <img src={source} alt={"Ảnh review " + image.slot + " của Listing Image"}
    data-review-quality={fallback ? "thumbnail" : image.previewUrl ? "webp" : "thumbnail"}
    decoding="async" loading="eager"
    onError={() => { if (!fallback && initial !== image.thumbnailUrl) setFallback(true); }} />;
}

const GALLERY_CACHE_MS = 30_000;
type GalleryCache = { identity: string; until: number; value: ListingImageGallery };


/** One lazy detail request per modal, never one request for each Picked row. */
export const ListingImageCell = memo(function ListingImageCell({item, onPreviewStart}: Props) {
  const [open, setOpen] = useState(false);
  const [gallery, setGallery] = useState<ListingImageGallery | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ListingImageOutput | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const galleryCache = useRef<GalleryCache | null>(null);
  const badges = (item.listingImages || [])
    .filter(b => safeBadge(b.garmentCode, b.count))
    .sort((a,b) => Number(b.garmentCode === "C1717") -
                    Number(a.garmentCode === "C1717") ||
                    a.garmentCode.localeCompare(b.garmentCode));
  const mainC1717Url = badges.find(b => b.garmentCode === "C1717" &&
    typeof b.mainThumbnailUrl === "string")?.mainThumbnailUrl;
  const galleryIdentity = item.listingKey + "|" + badges.map(b =>
    b.garmentCode + ":" + b.count + ":" + (b.mainThumbnailUrl || "")).join("|");
  const selectedGarment = gallery?.groups.find(group => group.images.some(image =>
    image.packId === selected?.packId && image.sha256 === selected?.sha256))?.garmentCode;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError("");
    const cached = galleryCache.current;
    if (cached?.identity === galleryIdentity && cached.until > Date.now()) {
      setGallery(cached.value);
      setSelected(cached.value.groups.find(group => group.garmentCode === "C1717")
        ?.images.find(image => image.slot === 1) ||
        cached.value.groups.flatMap(group => group.images)[0] || null);
      return () => controller.abort();
    }
    setGallery(null);
    setSelected(null);
    void spyFetch<ListingImageGallery>(
      "/listings/" + encodeURIComponent(item.listingKey) + "/listing-images",
      { signal: controller.signal }
    ).then(result => {
      if (!controller.signal.aborted) {
        galleryCache.current = { identity: galleryIdentity,
          until: Date.now() + GALLERY_CACHE_MS, value: result };
        setGallery(result);
        setSelected(result.groups.find(group => group.garmentCode === "C1717")
          ?.images.find(image => image.slot === 1) ||
          result.groups.flatMap(group => group.images)[0] || null);
      }
    }).catch(reason => {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Không thể đọc ảnh ImageGen đã lưu");
    });
    return () => controller.abort();
  }, [open, galleryIdentity, item.listingKey, retryKey]);

  useEffect(() => {
    if (!open) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [open]);

  const openGallery = () => { onPreviewStart?.(); setOpen(true); };
  return <>
    <td className="etsy-listing-image-cell">
      <div className="etsy-listing-image-layout">
        <div className="etsy-listing-image-primary">
          {mainC1717Url ? <button type="button"
            className="etsy-listing-image-main"
            onClick={openGallery}
            aria-label={"Xem ảnh Main C1717 cho " + item.pageTitle}
            title="C1717 · Ảnh Main đã tạo và lưu vào CAM ImageGen Designs">
            <EtsyDeferredImage src={mainC1717Url} alt="Ảnh Main C1717" />
          </button> : <span className="etsy-listing-image-pending"
            title="Chưa có ảnh Main C1717 được đồng bộ từ Image Extension">Chưa có ảnh</span>}
        </div>
        {badges.length > 0 && <div className="etsy-listing-image-badges"
          aria-label="Ảnh đã lưu theo mã áo">
          {badges.map(b => <button type="button"
            className="etsy-listing-image-badge" key={b.garmentCode}
            title={b.count + " ảnh " + b.garmentCode + " đã lưu từ CAM Image Extension"}
            aria-label={"Xem " + b.count + " ảnh Listing Image mẫu " + b.garmentCode}
            onClick={openGallery}>
            <span>{b.count}</span> {b.garmentCode}
          </button>)}
        </div>}
      </div>
    </td>
    {open && createPortal(
      <div className="etsy-listing-image-overlay"
        role="presentation"
        onMouseDown={event => { if (event.target === event.currentTarget) setOpen(false); }}>
        <section role="dialog" aria-modal="true"
          aria-label={"Listing Image đã lưu cho " + item.pageTitle}
          className={"etsy-listing-image-dialog" + (gallery && gallery.total <= 2 ? " is-sparse" : "")}>
          <header className="etsy-listing-image-dialog-head">
            <h2>Listing Image</h2>
            <button type="button" className="etsy-listing-image-close"
              onClick={() => setOpen(false)} aria-label="Đóng Listing Image">×</button>
          </header>
          {!gallery && !error && <div className="etsy-listing-image-notice" role="status">Đang tải ảnh…</div>}
          {error && <div className="etsy-listing-image-notice is-error" role="alert">
            {error} <button type="button" onClick={() => {
              galleryCache.current = null;
              setRetryKey(value => value + 1);
            }}>Thử lại</button>
          </div>}
          {gallery && <div className="etsy-listing-image-body">
            {gallery.total === 0 ? <div className="etsy-listing-image-notice">
              Chưa có ảnh đã lưu.
            </div> : <div className={"etsy-listing-image-workspace" +
              (gallery.total <= 2 ? " is-sparse" : "")}>
              <section className="etsy-listing-image-preview" aria-label="Ảnh xem trước">
                {selected && <div className="etsy-listing-image-review-frame">
                  <a className="etsy-listing-image-fullsize" href={selected.originalUrl}
                    target="_blank" rel="noopener noreferrer" title="Mở PNG gốc"
                    aria-label={"Mở PNG gốc " + (selectedGarment || "") + " ảnh " + selected.slot}>
                    <ListingReviewPreview key={selected.packId + ":" + selected.sha256}
                      image={selected} />
                    <span className="etsy-listing-image-open-icon" aria-hidden="true">↗</span>
                  </a>
                </div>}
              </section>
              <aside className="etsy-listing-image-library" aria-label="Danh sách ảnh theo sản phẩm">
                {gallery.groups.map(group => <section
                  className="etsy-listing-image-group" key={group.garmentCode}>
                  <h3 className="etsy-listing-image-group-heading">{group.garmentCode}</h3>
                  {Array.from(new Set(group.images.map(image => image.packId))).map(packId => {
                    const designImages = group.images.filter(image => image.packId === packId)
                      .sort((a, b) => a.slot - b.slot);
                    return <div className="etsy-listing-image-design" key={packId}>
                      <div className="etsy-listing-image-grid">
                        {designImages.map(image => <button type="button"
                          className={"etsy-listing-image-tile" +
                            (selected?.packId === image.packId && selected?.sha256 === image.sha256 ? " is-selected" : "")}
                          key={image.packId + "-" + image.slot}
                          title={group.garmentCode + " · Ảnh " + String(image.slot).padStart(2, "0")}
                          aria-label={"Xem ảnh " + image.slot + " / " + group.garmentCode}
                          aria-pressed={selected?.packId === image.packId && selected?.sha256 === image.sha256}
                          onClick={() => setSelected(image)}>
                          <EtsyDeferredImage src={image.thumbnailUrl} alt="" />
                          <span className="etsy-listing-image-slot" aria-hidden="true">
                            {String(image.slot).padStart(2, "0")}
                          </span>
                        </button>)}
                      </div>
                    </div>;
                  })}
                </section>)}
              </aside>
            </div>}
          </div>}
        </section>
      </div>, document.body
    )}
  </>;
});