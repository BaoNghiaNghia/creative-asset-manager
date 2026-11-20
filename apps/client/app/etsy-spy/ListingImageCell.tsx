import { memo, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import { spyFetch, type Listing, type ListingImageGallery, type ListingImageOutput, type TikTokListingView } from "./api";
import { formatTikTokDraft } from "./TikTokListingCell";
import { EtsyDeferredImage } from "./EtsyDeferredImage";
import "./ListingImageCell.css";

const MODEL = /^(C1717|G500|G180|G185)$/;
const safeBadge = (code: string, count: number) =>
  MODEL.test(code) && Number.isSafeInteger(count) && count > 0 && count <= 1000;

type Props = {
  item: Listing;
  onPreviewStart?: () => void;
  canDelete?: boolean;
  onImageDeleted?: () => void;
};

// A missing WebP conversion must not trigger an original 1254px+ PNG fetch.
// Use the already saved 192px thumbnail as the fail-safe visual fallback.
function ListingReviewPreview({ image, transform }: { image: ListingImageOutput; transform?: string }) {
  const [fallback, setFallback] = useState(false);
  const initial = image.previewUrl || image.thumbnailUrl;
  const source = fallback ? image.thumbnailUrl : initial;
  return <img src={source} alt={"Ảnh review " + image.slot + " của Listing Image"}
    data-review-quality={fallback ? "thumbnail" : image.previewUrl ? "webp" : "thumbnail"}
    decoding="async" loading="eager" draggable={false} style={{ transform }}
    onError={() => { if (!fallback && initial !== image.thumbnailUrl) setFallback(true); }} />;
}

type ImageZoom = { scale: number; x: number; y: number };
const FIT_ZOOM: ImageZoom = { scale: 1, x: 0, y: 0 };
const MAX_ZOOM = 5;
const constrainOffset = (position: number, scale: number, extent: number) => {
  const limit = Math.max(0, (scale - 1) * extent / 2);
  return Math.max(-limit, Math.min(limit, position));
};

/** Zoom on the preview only: wheel anchors the pixel under the cursor.
 * The middle mouse button resets to fit; switching images remounts the view.
 * Using a non-passive native listener avoids Chrome's passive React wheel trap.
 */
function ListingZoomPreview({ image, garmentCode }: {
  image: ListingImageOutput; garmentCode: string;
}) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{
    pointerId: number; originX: number; originY: number;
    startX: number; startY: number; moved: boolean;
  } | null>(null);
  const suppressClickRef = useRef(false);
  const [zoom, setZoom] = useState<ImageZoom>(FIT_ZOOM);
  const [dragging, setDragging] = useState(false);
  // Product photos fill the wide center card by default. Full-view mode
  // remains available for infographics and prints near the image edges.
  const [fitMode, setFitMode] = useState<"cover" | "contain">("cover");
  const resetZoom = () => {
    dragRef.current = null;
    suppressClickRef.current = false;
    setDragging(false);
    setZoom(FIT_ZOOM);
  };

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const zoomOnWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = frame.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0 || !Number.isFinite(event.deltaY)) return;
      const delta = event.deltaMode === 1 ? event.deltaY * 16 :
        event.deltaMode === 2 ? event.deltaY * rect.height : event.deltaY;
      const cursorX = event.clientX - rect.left - rect.width / 2;
      const cursorY = event.clientY - rect.top - rect.height / 2;
      setZoom(previous => {
        const nextScale = Math.max(1, Math.min(MAX_ZOOM,
          previous.scale * Math.exp(-delta * 0.0018)));
        if (nextScale === previous.scale) return previous;
        if (nextScale <= 1.001) return FIT_ZOOM;
        const ratio = nextScale / previous.scale;
        return {
          scale: nextScale,
          x: constrainOffset(cursorX - (cursorX - previous.x) * ratio, nextScale, rect.width),
          y: constrainOffset(cursorY - (cursorY - previous.y) * ratio, nextScale, rect.height),
        };
      });
    };
    frame.addEventListener("wheel", zoomOnWheel, { passive: false });
    return () => frame.removeEventListener("wheel", zoomOnWheel);
  }, []);

  const onPointerDown = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    if (event.button === 1) {
      event.preventDefault();
      resetZoom();
      return;
    }
    if (event.button !== 0 || zoom.scale <= 1) return;
    event.preventDefault();
    dragRef.current = {
      pointerId: event.pointerId, originX: event.clientX, originY: event.clientY,
      startX: zoom.x, startY: zoom.y, moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(true);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    const dx = event.clientX - drag.originX;
    const dy = event.clientY - drag.originY;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    const rect = frameRef.current?.getBoundingClientRect();
    if (!rect) return;
    setZoom(previous => ({
      ...previous,
      x: constrainOffset(drag.startX + dx, previous.scale, rect.width),
      y: constrainOffset(drag.startY + dy, previous.scale, rect.height),
    }));
  };
  const onPointerEnd = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    if (!dragRef.current || event.pointerId !== dragRef.current.pointerId) return;
    suppressClickRef.current = dragRef.current.moved;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const onAuxClick = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 1) return;
    event.preventDefault(); // Never open the PNG on middle click.
    event.stopPropagation();
    resetZoom();
  };

  return <div className="etsy-listing-image-review-frame" ref={frameRef}
    data-zoom-scale={zoom.scale.toFixed(3)} data-fit-mode={fitMode}>
    <a className={"etsy-listing-image-fullsize" +
      (zoom.scale > 1 ? " is-zoomed" : "") + (dragging ? " is-dragging" : "")}
      href={image.originalUrl}
      target="_blank" rel="noopener noreferrer" title="Mở PNG gốc"
      aria-label={"Mở PNG gốc " + garmentCode + " ảnh " + image.slot}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd}
      onMouseDown={event => { if (event.button === 1) { event.preventDefault(); resetZoom(); } }}
      onAuxClick={onAuxClick}
      onDragStart={event => event.preventDefault()}
      onClick={event => {
        if (suppressClickRef.current) {
          event.preventDefault();
          suppressClickRef.current = false;
        }
      }}>
      <ListingReviewPreview image={image}
        transform={`translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})`} />
      <span className="etsy-listing-image-open-icon" aria-hidden="true">↗</span>
    </a>
    <button type="button" className="etsy-listing-image-fit-toggle"
      aria-label={fitMode === "cover" ? "Xem toàn bộ ảnh, không cắt" : "Lấp đầy khung preview"}
      aria-pressed={fitMode === "contain"}
      title={fitMode === "cover" ? "Xem toàn ảnh" : "Lấp đầy khung"}
      onClick={() => {
        resetZoom();
        setFitMode(current => current === "cover" ? "contain" : "cover");
      }}>
      {fitMode === "cover" ? "Toàn ảnh" : "Đầy khung"}
    </button>
  </div>;
}

const GALLERY_CACHE_MS = 30_000;
type GalleryCache = { identity: string; until: number; value: ListingImageGallery };

const SEO_ACTIVE = new Set(["pending", "retry", "processing"]);
const SEO_STATUS: Record<string, string> = {
  not_queued: "Chưa tạo", pending: "Đang chờ", retry: "Đang thử lại",
  processing: "Đang tạo",
  failed: "Tạo lỗi", cancelled: "Đã hủy",
};

/** Render the existing TikTok listing draft, never an invented SEO draft.
 * Detail is fetched only after this gallery is opened, not on Picked rows.
 */
function TikTokSeoAside({ item }: { item: Listing }) {
  const [detail, setDetail] = useState<TikTokListingView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const rowSeo = item.tiktokListing;
  const hasDetail = Boolean(rowSeo && (rowSeo.status !== "not_queued" ||
    rowSeo.title_preview || rowSeo.search_terms_preview?.length));

  useEffect(() => {
    if (!hasDetail) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void spyFetch<TikTokListingView>(
      "/listings/" + encodeURIComponent(item.listingKey) + "/tiktok-listing",
      { signal: controller.signal },
    ).then(result => {
      if (!controller.signal.aborted) setDetail(result);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(
        reason instanceof Error ? reason.message : "Không thể tải TikTok SEO");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [item.listingKey, hasDetail, retry]);

  const status = detail?.status || rowSeo?.status || "not_queued";
  useEffect(() => {
    if (!hasDetail || !SEO_ACTIVE.has(status)) return;
    const timer = window.setTimeout(() => setRetry(value => value + 1), 15_000);
    return () => window.clearTimeout(timer);
  }, [hasDetail, status, retry]);

  const draft = detail?.result || detail?.previous_draft;
  const title = draft?.title?.trim() || rowSeo?.title_preview?.trim() || "";
  const terms = [...new Set((draft?.search_terms || rowSeo?.search_terms_preview || [])
    .filter((value): value is string => typeof value === "string")
    .map(value => value.trim()).filter(Boolean))];
  const description = draft?.description?.trim() || "";
  const onCopy = async () => {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(formatTikTokDraft(draft));
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopied(false);
      setCopyError(true);
    }
  };

  return <aside className="etsy-listing-image-seo" aria-label="TikTok SEO">
    <div className="etsy-listing-image-seo-topline">
      <span>TIKTOK SHOP · SEO</span>
      {status !== "needs_fact_check" && <span
        className="etsy-listing-image-seo-state" data-seo-status={status}>
        {SEO_STATUS[status] || status}
      </span>}
    </div>
    {title ? <section className="etsy-listing-image-seo-section is-title">
      <span className="etsy-listing-image-seo-label">Product title</span>
      <h3>{title}</h3>
    </section> : <div className="etsy-listing-image-seo-empty">
      <strong>Chưa có bản nháp TikTok SEO</strong>
      <p>{SEO_ACTIVE.has(status) ? "Đang xử lý nội dung SEO. Ảnh đã lưu vẫn có thể xem trước." :
        "Khi Listing Optimizer tạo xong, nội dung SEO sẽ hiển thị tại đây."}</p>
    </div>}
    {terms.length > 0 && <section className="etsy-listing-image-seo-section">
      <h4>Search terms <small>{terms.length}</small></h4>
      <div className="etsy-listing-image-seo-terms">
        {terms.map(term => <span key={term}>{term}</span>)}
      </div>
    </section>}
    {!!draft?.highlights?.length && <section className="etsy-listing-image-seo-section">
      <h4>Product highlights</h4>
      <ul>{draft.highlights.map((value, i) => <li key={i}>{value}</li>)}</ul>
    </section>}
    {description && <section className="etsy-listing-image-seo-section">
      <h4>Product description</h4>
      <p className="etsy-listing-image-seo-description">
        {description.length > 260 ? description.slice(0, 260).trimEnd() + "…" : description}
      </p>
      {description.length > 260 && <details className="etsy-listing-image-seo-more">
        <summary>Xem toàn bộ mô tả</summary>
        <p>{description}</p>
      </details>}
    </section>}
    {loading && <p className="etsy-listing-image-seo-activity" role="status">Đang tải TikTok SEO…</p>}
    {error && <div className="etsy-listing-image-seo-error" role="alert">
      {error} <button type="button" onClick={() => setRetry(v => v + 1)}>Thử lại</button>
    </div>}
    <div className="etsy-listing-image-seo-actions">
      <button type="button" onClick={() => void onCopy()} disabled={!draft}>
        {copied ? "Đã sao chép" : "Sao chép TikTok SEO"}
      </button>
      <span>Chỉ là bản nháp · chưa xuất bản</span>
      {copyError && <small role="alert">Không thể sao chép tự động. Hãy thử lại.</small>}
    </div>
  </aside>;
}

/** One lazy detail request per modal, never one request for each Picked row. */
export const ListingImageCell = memo(function ListingImageCell({item, onPreviewStart, canDelete, onImageDeleted}: Props) {
  const [open, setOpen] = useState(false);
  const [gallery, setGallery] = useState<ListingImageGallery | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ListingImageOutput | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<ListingImageOutput | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [deleteNotice, setDeleteNotice] = useState("");
  const galleryCache = useRef<GalleryCache | null>(null);
  const badges = (item.listingImages || [])
    .filter(b => safeBadge(b.garmentCode, b.count))
    .sort((a,b) => Number(b.garmentCode === "C1717") -
                    Number(a.garmentCode === "C1717") ||
                    a.garmentCode.localeCompare(b.garmentCode));
  // Prefer a genuine C1717 Main when available; otherwise show a saved
  // thumbnail from any position/model. A finished slot 02+ is still a
  // visible ImageGen asset even while Main is pending publication.
  const primaryBadge = badges.find(b => b.garmentCode === "C1717" &&
    (b.mainThumbnailUrl || b.previewThumbnailUrl)) ||
    badges.find(b => b.mainThumbnailUrl || b.previewThumbnailUrl);
  const primaryUrl = primaryBadge?.mainThumbnailUrl || primaryBadge?.previewThumbnailUrl;
  const isMain = Boolean(primaryBadge?.mainThumbnailUrl);
  const galleryIdentity = item.listingKey + "|" + badges.map(b =>
    b.garmentCode + ":" + b.count + ":" + (b.reviewCount || 0) +
    ":" + (b.mainThumbnailUrl || "") + ":" +
    (b.previewThumbnailUrl || "") + ":" + (b.previewSlot || "")).join("|");
  const selectedGarment = gallery?.groups.find(group => group.images.some(image =>
    image.packId === selected?.packId && image.sha256 === selected?.sha256))?.garmentCode;
  const orderedImages = gallery?.groups.flatMap(group => group.images) || [];
  const deleteSelected = async () => {
    if (!deleteTarget || deleting || !gallery || !canDelete) return;
    const target = deleteTarget;
    setDeleting(true);
    setDeleteError("");
    try {
      const result = await spyFetch<{
        ok: boolean; deleted: boolean; drive_deleted: boolean;
        packId: string; slot: number; sha256: string;
      }>("/listings/" + encodeURIComponent(item.listingKey) +
        "/listing-images/" + encodeURIComponent(target.packId) +
        "/" + target.slot + "?sha256=" + encodeURIComponent(target.sha256), {
        method: "DELETE",
      });
      if (!result.ok || !result.deleted || !result.drive_deleted ||
        result.packId !== target.packId || result.slot !== target.slot ||
        result.sha256 !== target.sha256) {
        throw new Error("CAM chưa xác nhận xóa đủ PNG gốc và thumbnail trên Google Drive.");
      }
      const after = gallery.groups.map(group => {
        const images = group.images.filter(image => !(
          image.packId === target.packId &&
          image.slot === target.slot &&
          image.sha256 === target.sha256
        ));
        return { ...group, images, count: images.length };
      }).filter(group => group.count > 0);
      const remaining = after.flatMap(group => group.images);
      const previousIndex = orderedImages.findIndex(image =>
        image.packId === target.packId && image.slot === target.slot &&
        image.sha256 === target.sha256);
      const updated = { ...gallery, groups: after, total: remaining.length };
      galleryCache.current = null;
      setGallery(updated);
      setSelected(remaining[Math.min(Math.max(0, previousIndex), remaining.length - 1)] || null);
      setDeleteTarget(null);
      setDeleteNotice("Đã xóa ảnh " + String(target.slot).padStart(2, "0") +
        " khỏi CAM và Google Drive. Slot trống được giữ lại để tạo ảnh mới sau.");
      onImageDeleted?.();
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message :
        "Không thể xóa ảnh trên Google Drive. Ảnh vẫn được giữ lại trong CAM.");
    } finally {
      setDeleting(false);
    }
  };
  const moveImage = (step: -1 | 1) => {
    if (orderedImages.length < 2) return;
    const index = orderedImages.findIndex(image =>
      image.packId === selected?.packId && image.slot === selected?.slot &&
      image.sha256 === selected?.sha256);
    setSelected(orderedImages[(index + step + orderedImages.length) % orderedImages.length]);
  };

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
      if (event.key === "Escape" && !deleting) {
        if (deleteTarget) setDeleteTarget(null);
        else setOpen(false);
      }
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [open, deleteTarget, deleting]);

  const openGallery = () => { onPreviewStart?.(); setOpen(true); };
  return <>
    <td className="etsy-listing-image-cell">
      <div className="etsy-listing-image-layout">
        <div className="etsy-listing-image-primary">
          {primaryUrl ? <button type="button"
            className="etsy-listing-image-main"
            onClick={openGallery}
            aria-label={"Xem " + (isMain ? "ảnh Main " : "ảnh ImageGen đã lưu ") +
              primaryBadge?.garmentCode + " cho " + item.pageTitle}
            title={primaryBadge?.garmentCode + (isMain
              ? " · Ảnh Main đã lưu vào CAM" : " · Ảnh đã đồng bộ từ ImageGen Extension")}>
            <EtsyDeferredImage src={primaryUrl}
              alt={isMain ? "Ảnh Main " + primaryBadge?.garmentCode
                : "Ảnh ImageGen " + primaryBadge?.garmentCode} />
          </button> : <span className="etsy-listing-image-pending"
            title="Chưa nhận được ảnh đã lưu từ ImageGen Extension">Chưa đồng bộ ảnh</span>}
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
        onMouseDown={event => {
          if (event.target === event.currentTarget && !deleting && !deleteTarget) setOpen(false);
        }}>
        <section role="dialog" aria-modal="true"
          aria-label={"Listing Image đã lưu cho " + item.pageTitle}
          className={"etsy-listing-image-dialog" + (gallery && gallery.total <= 2 ? " is-sparse" : "")}>
          <header className="etsy-listing-image-dialog-head">
            <h2>Listing Image</h2>
            <div className="etsy-listing-image-head-actions">
              {canDelete && selected && <button type="button"
                className="etsy-listing-image-delete-trigger"
                disabled={deleting}
                title={"Xóa vĩnh viễn ảnh " + String(selected.slot).padStart(2, "0") +
                  " khỏi CAM và Google Drive"}
                aria-label={"Xóa ảnh " + String(selected.slot).padStart(2, "0") +
                  " " + (selectedGarment || "")}
                onClick={() => {
                  setDeleteError("");
                  setDeleteNotice("");
                  setDeleteTarget(selected);
                }}>
                Xóa ảnh {String(selected.slot).padStart(2, "0")}
              </button>}
              <button type="button" className="etsy-listing-image-close"
                disabled={deleting} onClick={() => setOpen(false)}
                aria-label="Đóng Listing Image">×</button>
            </div>
          </header>
          {deleteNotice && <p className="etsy-listing-image-delete-notice" role="status">
            {deleteNotice}
          </p>}
          {deleteTarget && <div className="etsy-listing-image-confirm-backdrop">
            <div className="etsy-listing-image-confirm" role="alertdialog"
              aria-modal="true" aria-labelledby="etsy-listing-image-delete-title"
              aria-describedby="etsy-listing-image-delete-description">
              <h3 id="etsy-listing-image-delete-title">
                Xóa ảnh {String(deleteTarget.slot).padStart(2, "0")}?
              </h3>
              <p id="etsy-listing-image-delete-description">
                Xóa đúng ảnh đã chọn khỏi CAM và xóa vĩnh viễn PNG gốc cùng
                thumbnail của ảnh này trên Google Drive. Các ảnh khác được giữ nguyên.
                Slot sẽ để trống cho lượt tạo lại sau, không tự tạo ngay lúc xóa.
              </p>
              {deleteError && <p className="etsy-listing-image-confirm-error" role="alert">
                {deleteError}
              </p>}
              <div className="etsy-listing-image-confirm-actions">
                <button type="button" disabled={deleting}
                  onClick={() => { setDeleteTarget(null); setDeleteError(""); }}>
                  Hủy
                </button>
                <button type="button" className="is-danger" disabled={deleting}
                  onClick={() => { void deleteSelected(); }}>
                  {deleting ? "Đang xóa trên Drive…" : "Xóa khỏi CAM và Google Drive"}
                </button>
              </div>
            </div>
          </div>}
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
              <section className="etsy-listing-image-preview" aria-label="Ảnh xem trước">
                {selected && <ListingZoomPreview key={selected.packId + ":" + selected.sha256}
                  image={selected} garmentCode={selectedGarment || ""} />}
                {orderedImages.length > 1 && <>
                  <button type="button" className="etsy-listing-image-nav is-prev"
                    onClick={() => moveImage(-1)} aria-label="Ảnh trước" title="Ảnh trước">‹</button>
                  <button type="button" className="etsy-listing-image-nav is-next"
                    onClick={() => moveImage(1)} aria-label="Ảnh tiếp theo" title="Ảnh tiếp theo">›</button>
                </>}
              </section>
              <TikTokSeoAside item={item} />
            </div>}
          </div>}
        </section>
      </div>, document.body
    )}
  </>;
});