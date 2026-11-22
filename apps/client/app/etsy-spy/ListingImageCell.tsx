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
const imageSelectionKey = (image: ListingImageOutput) =>
  image.packId + ":" + image.slot + ":" + image.sha256;

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
    data-zoom-scale={zoom.scale.toFixed(3)}>
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
  const [deleteMode, setDeleteMode] = useState(false);
  const [markedImageKeys, setMarkedImageKeys] = useState<string[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteProgress, setDeleteProgress] = useState<{done: number; total: number} | null>(null);
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
  // Only the representative garment is shown in the dense table. Remaining
  // garments and their exact counts are available in the existing gallery.
  const leadingBadge = primaryBadge || badges[0];
  const otherGarmentCount = Math.max(0, badges.length - (leadingBadge ? 1 : 0));
  const allGarmentsTooltip = badges.map(b => b.count + " ảnh " + b.garmentCode).join(" · ");
  const galleryIdentity = item.listingKey + "|" + badges.map(b =>
    b.garmentCode + ":" + b.count + ":" + (b.reviewCount || 0) +
    ":" + (b.mainThumbnailUrl || "") + ":" +
    (b.previewThumbnailUrl || "") + ":" + (b.previewSlot || "")).join("|");
  const selectedGarment = gallery?.groups.find(group => group.images.some(image =>
    image.packId === selected?.packId && image.sha256 === selected?.sha256))?.garmentCode;
  const orderedImages = gallery?.groups.flatMap(group => group.images) || [];
  const markedSet = new Set(markedImageKeys);
  const deleteQueue = orderedImages.filter(image => markedSet.has(imageSelectionKey(image)));
  const allMarked = orderedImages.length > 0 && deleteQueue.length === orderedImages.length;
  const closeGallery = () => {
    setOpen(false);
    setDeleteMode(false);
    setMarkedImageKeys([]);
    setConfirmDelete(false);
    setDeleteError("");
    setDeleteProgress(null);
  };
  // Reuse the existing tenant-checked, SHA-guarded single-image endpoint.
  // Delete serially so we can report partial Drive failures without hiding
  // or marking other files as deleted. Never delete a whole Drive folder.
  const deleteSelected = async () => {
    if (!confirmDelete || deleting || !gallery || !canDelete || !deleteQueue.length) return;
    const targets = [...deleteQueue];
    const deletingWholeDesign = targets.length === orderedImages.length;
    const completed: ListingImageOutput[] = [];
    let failure = "";
    setDeleting(true);
    setDeleteError("");
    setDeleteProgress({done: 0, total: targets.length});
    try {
      for (const target of targets) {
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
            throw new Error("CAM chưa xác nhận đã xóa cả PNG gốc và thumbnail trên Drive.");
          }
          completed.push(target);
          setDeleteProgress({done: completed.length, total: targets.length});
        } catch (reason) {
          failure = reason instanceof Error ? reason.message :
            "Không thể xác nhận thao tác xóa ảnh trên Google Drive.";
          break; // Do not retry an uncertain remote deletion automatically.
        }
      }
      if (completed.length) {
        const deletedKeys = new Set(completed.map(imageSelectionKey));
        const after = gallery.groups.map(group => {
          const images = group.images.filter(image => !deletedKeys.has(imageSelectionKey(image)));
          return {...group, images, count: images.length};
        }).filter(group => group.count > 0);
        const remaining = after.flatMap(group => group.images);
        const formerIndex = orderedImages.findIndex(image =>
          deletedKeys.has(imageSelectionKey(image)));
        galleryCache.current = null;
        setGallery({...gallery, groups: after, total: remaining.length});
        setSelected(previous => previous && !deletedKeys.has(imageSelectionKey(previous))
          ? previous : remaining[Math.min(formerIndex, remaining.length - 1)] || null);
        setMarkedImageKeys(current => current.filter(key => !deletedKeys.has(key)));
        onImageDeleted?.();
      }
      if (failure) {
        setDeleteError("Đã xóa " + completed.length + "/" + targets.length +
          " ảnh. Dừng khi gặp lỗi: " + failure +
          " Các ảnh chưa xóa vẫn được chọn; hãy kiểm tra lại trước khi thử tiếp.");
      } else {
        setConfirmDelete(false);
        setDeleteMode(false);
        setMarkedImageKeys([]);
        setDeleteNotice((deletingWholeDesign ? "Đã xóa toàn bộ " : "Đã xóa ") +
          targets.length + " ảnh Listing Image của design này khỏi CAM và Google Drive. " +
          "Các slot trống sẽ được tạo lại ở lượt sau, không tự chạy ngay.");
      }
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
      if (event.key !== "Escape" || deleting) return;
      if (confirmDelete) {
        setConfirmDelete(false);
        setDeleteError("");
        setDeleteProgress(null);
      } else if (deleteMode) {
        setDeleteMode(false);
        setMarkedImageKeys([]);
      } else {
        closeGallery();
      }
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [open, confirmDelete, deleteMode, deleting]);

  const openGallery = () => { onPreviewStart?.(); setOpen(true); };
  return <>
    <td className="etsy-listing-image-cell">
      <div className={"etsy-listing-image-layout" + (!leadingBadge ? " is-empty" : "")}>
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
            title={badges.length
              ? "Đã có ảnh được lưu nhưng chưa có thumbnail để xem trong bảng. " + allGarmentsTooltip
              : "Chưa nhận được ảnh đã lưu từ ImageGen Extension"}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
              strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <circle cx="8.5" cy="9" r="1.5" />
              <path d="m5 18 5.5-5.5 3.5 3 2.5-2.5 2.5 3" />
            </svg>
            <span className="etsy-listing-image-pending-label">
              {badges.length ? "Chưa có preview" : "Chưa có ảnh"}
            </span>
          </span>}
        </div>
        {leadingBadge && <div className="etsy-listing-image-badges"
          aria-label="Số ảnh theo mã áo">
          <button type="button" className="etsy-listing-image-badge"
            title={allGarmentsTooltip + " · Mở thư viện ảnh"}
            aria-label={"Xem " + leadingBadge.count + " ảnh Listing Image mẫu " +
              leadingBadge.garmentCode + (otherGarmentCount
                ? ", và " + otherGarmentCount + " mẫu áo khác" : "")}
            onClick={openGallery}>
            <span className="etsy-listing-image-count">{leadingBadge.count} ảnh</span>
            <span className="etsy-listing-image-garment">{leadingBadge.garmentCode}</span>
          </button>
          {otherGarmentCount > 0 && <button type="button"
            className="etsy-listing-image-more"
            title={allGarmentsTooltip}
            aria-label={"Xem " + otherGarmentCount + " mẫu áo khác. " + allGarmentsTooltip}
            onClick={openGallery}>+{otherGarmentCount} mẫu</button>}
        </div>}
      </div>
    </td>
    {open && createPortal(
      <div className="etsy-listing-image-overlay"
        role="presentation"
        onMouseDown={event => {
          if (event.target === event.currentTarget && !deleting && !confirmDelete) closeGallery();
        }}>
        <section role="dialog" aria-modal="true"
          aria-label={"Listing Image đã lưu cho " + item.pageTitle}
          className={"etsy-listing-image-dialog" + (gallery && gallery.total <= 2 ? " is-sparse" : "")}>
          <header className="etsy-listing-image-dialog-head">
            <h2>Listing Image</h2>
            <div className="etsy-listing-image-head-actions">
              {canDelete && orderedImages.length > 0 && (deleteMode ? <>
                <span className="etsy-listing-image-selected-count" aria-live="polite">
                  {deleteQueue.length}/{orderedImages.length} ảnh đã chọn
                </span>
                <button type="button" className="etsy-listing-image-select-all"
                  disabled={deleting} aria-pressed={allMarked}
                  onClick={() => setMarkedImageKeys(allMarked ? [] :
                    orderedImages.map(imageSelectionKey))}>
                  {allMarked ? "Bỏ chọn tất cả" : "Chọn tất cả"}
                </button>
                <button type="button" className="etsy-listing-image-delete-trigger is-danger"
                  disabled={deleting || deleteQueue.length === 0}
                  onClick={() => {
                    setDeleteError("");
                    setDeleteProgress(null);
                    setConfirmDelete(true);
                  }}>
                  Xóa {deleteQueue.length} ảnh
                </button>
                <button type="button" className="etsy-listing-image-select-cancel"
                  disabled={deleting} onClick={() => {
                    setDeleteMode(false);
                    setMarkedImageKeys([]);
                    setDeleteError("");
                  }}>Hủy chọn</button>
              </> : <button type="button" className="etsy-listing-image-delete-trigger"
                disabled={deleting}
                onClick={() => {
                  setDeleteMode(true);
                  setMarkedImageKeys([]);
                  setDeleteNotice("");
                  setDeleteError("");
                }}>
                Chọn ảnh để xóa
              </button>)}
              <button type="button" className="etsy-listing-image-close"
                disabled={deleting} onClick={closeGallery}
                aria-label="Đóng Listing Image">×</button>
            </div>
          </header>
          {deleteNotice && <p className="etsy-listing-image-delete-notice" role="status">
            {deleteNotice}
          </p>}
          {confirmDelete && deleteQueue.length > 0 &&
            <div className="etsy-listing-image-confirm-backdrop">
              <div className="etsy-listing-image-confirm" role="alertdialog"
                aria-modal="true" aria-labelledby="etsy-listing-image-delete-title"
                aria-describedby="etsy-listing-image-delete-description">
                <h3 id="etsy-listing-image-delete-title">
                  {allMarked
                    ? "Xóa toàn bộ " + deleteQueue.length + " ảnh của design này?"
                    : "Xóa " + deleteQueue.length + " ảnh đã chọn?"}
                </h3>
                <p id="etsy-listing-image-delete-description">
                  Chỉ xóa Listing Image thuộc design đang mở, gồm PNG gốc và
                  thumbnail trên Google Drive cùng bản ghi trong CAM.
                  {allMarked
                    ? " Bạn đang chọn tất cả ảnh Listing Image của design này."
                    : " Các ảnh không được chọn sẽ được giữ nguyên."}
                  {" "}Ảnh thiết kế gốc không bị xóa. Các slot trống chỉ được tạo lại ở lượt sau.
                </p>
                <p className="etsy-listing-image-confirm-selection">
                  {deleteQueue.length} / {orderedImages.length} ảnh của design này
                  {gallery?.groups.map(group => {
                    const count = group.images.filter(image =>
                      markedSet.has(imageSelectionKey(image))).length;
                    return count ? <span key={group.garmentCode}>
                      {group.garmentCode}: {count} ảnh
                    </span> : null;
                  })}
                </p>
                {deleteProgress && deleting && <p role="status">
                  Đang xóa trên Drive: {deleteProgress.done}/{deleteProgress.total} ảnh…
                </p>}
                {deleteError && <p className="etsy-listing-image-confirm-error" role="alert">
                  {deleteError}
                </p>}
                <div className="etsy-listing-image-confirm-actions">
                  <button type="button" disabled={deleting}
                    onClick={() => {
                      setConfirmDelete(false);
                      setDeleteError("");
                      setDeleteProgress(null);
                    }}>Hủy</button>
                  <button type="button" className="is-danger"
                    disabled={deleting || deleteQueue.length === 0}
                    onClick={() => { void deleteSelected(); }}>
                    {deleting ? "Đang xóa…" :
                      allMarked ? "Xóa tất cả " + deleteQueue.length + " ảnh" :
                      "Xóa " + deleteQueue.length + " ảnh đã chọn"}
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
                            (selected?.packId === image.packId && selected?.sha256 === image.sha256 ? " is-selected" : "") +
                            (deleteMode && markedSet.has(imageSelectionKey(image)) ? " is-marked" : "")}
                          key={image.packId + "-" + image.slot}
                          disabled={deleting}
                          title={group.garmentCode + " · Ảnh " + String(image.slot).padStart(2, "0")}
                          aria-label={deleteMode
                            ? (markedSet.has(imageSelectionKey(image)) ? "Bỏ chọn ảnh " : "Chọn ảnh ") +
                              image.slot + " / " + group.garmentCode + " để xóa"
                            : "Xem ảnh " + image.slot + " / " + group.garmentCode}
                          aria-pressed={deleteMode
                            ? markedSet.has(imageSelectionKey(image))
                            : selected?.packId === image.packId && selected?.sha256 === image.sha256}
                          onClick={() => {
                            setSelected(image);
                            if (deleteMode) setMarkedImageKeys(current => {
                              const key = imageSelectionKey(image);
                              return current.includes(key) ?
                                current.filter(value => value !== key) : [...current, key];
                            });
                          }}>
                          <EtsyDeferredImage src={image.thumbnailUrl} alt="" />
                          {deleteMode && <span
                            className={"etsy-listing-image-select-mark" +
                              (markedSet.has(imageSelectionKey(image)) ? " is-checked" : "")}
                            aria-hidden="true">
                            {markedSet.has(imageSelectionKey(image)) ? "✓" : ""}
                          </span>}
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