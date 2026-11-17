import { memo, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { spyFetch, type Listing, type ListingImageGallery, type ListingImageOutput } from "./api";
import "./ListingImageCell.css";

const MODEL = /^(C1717|G500|G180|G185)$/;
const safeBadge = (code: string, count: number) =>
  MODEL.test(code) && Number.isSafeInteger(count) && count > 0 && count <= 1000;

type Props = { item: Listing; onPreviewStart?: () => void };

/** One lazy detail request per modal, never one request for each Picked row. */
export const ListingImageCell = memo(function ListingImageCell({item, onPreviewStart}: Props) {
  const [open, setOpen] = useState(false);
  const [gallery, setGallery] = useState<ListingImageGallery | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ListingImageOutput | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const badges = (item.listingImages || [])
    .filter(b => safeBadge(b.garmentCode, b.count))
    .sort((a,b) => a.garmentCode.localeCompare(b.garmentCode));

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setGallery(null);
    setError("");
    setSelected(null);
    void spyFetch<ListingImageGallery>(
      "/listings/" + encodeURIComponent(item.listingKey) + "/listing-images",
      { signal: controller.signal }
    ).then(result => {
      if (!controller.signal.aborted) {
        setGallery(result);
        setSelected(result.groups.flatMap(group => group.images)[0] || null);
      }
    }).catch(reason => {
      if (!controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Không thể đọc ảnh ImageGen đã lưu");
    });
    return () => controller.abort();
  }, [open, item.listingKey, retryKey]);

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
      {badges.length ? <div className="etsy-listing-image-badges">
        {badges.map(b => <button type="button"
          className="etsy-listing-image-badge" key={b.garmentCode}
          title={b.count + " ảnh " + b.garmentCode + " đã lưu từ CAM Image Extension"}
          aria-label={"Xem " + b.count + " ảnh Listing Image mẫu " + b.garmentCode}
          onClick={openGallery}>
          <span>{b.count}</span> {b.garmentCode}
        </button>)}
      </div> : <span className="etsy-muted" title="Chưa có PNG output đã đồng bộ từ ImageGen">—</span>}
    </td>
    {open && createPortal(
      <div className="etsy-listing-image-overlay"
        role="presentation"
        onMouseDown={event => { if (event.target === event.currentTarget) setOpen(false); }}>
        <section role="dialog" aria-modal="true"
          aria-label={"Listing Image đã lưu cho " + item.pageTitle}
          className="etsy-listing-image-dialog">
          <header className="etsy-listing-image-dialog-head">
            <div>
              <span className="etsy-listing-image-kicker">CAM Image Extension / Saved outputs</span>
              <h2>Listing Image <span>#{item.listingKey}</span></h2>
              <p title={item.pageTitle}>{item.pageTitle}</p>
            </div>
            <button type="button" className="etsy-listing-image-close"
              onClick={() => setOpen(false)} aria-label="Đóng Listing Image">×</button>
          </header>
          {!gallery && !error && <div className="etsy-listing-image-notice" role="status">Đang tải danh sách PNG đã lưu…</div>}
          {error && <div className="etsy-listing-image-notice is-error" role="alert">
            {error} <button type="button" onClick={() => setRetryKey(value => value + 1)}>Thử lại</button>
          </div>}
          {gallery && <div className="etsy-listing-image-body">
            <div className="etsy-listing-image-overview">
              <strong>{gallery.total} ảnh output đã lưu</strong>
              <div>{gallery.groups.map(group => <span key={group.garmentCode}
                className="etsy-listing-image-summary">{group.count} {group.garmentCode}</span>)}</div>
            </div>
            {gallery.total === 0 ? <div className="etsy-listing-image-notice">
              Chưa có ảnh output hoàn tất được Extension đồng bộ. Các ảnh đang chờ tạo không được tính.
            </div> : <>
              {selected && <div className="etsy-listing-image-large">
                <img src={selected.originalUrl} alt={"Ảnh ImageGen slot " + selected.slot}
                  loading="eager" />
                <div>
                  <strong>Ảnh {String(selected.slot).padStart(2,"0")}</strong>
                  <small>{selected.width || "—"} × {selected.height || "—"} px</small>
                  <a href={selected.originalUrl} target="_blank" rel="noopener noreferrer">
                    Xem PNG gốc
                  </a>
                </div>
              </div>}
              {gallery.groups.map(group => <section className="etsy-listing-image-group" key={group.garmentCode}>
                <h3>{group.garmentCode} <span>{group.count} ảnh đã lưu</span></h3>
                <div className="etsy-listing-image-grid">
                  {group.images.map(image => <button type="button"
                    className={"etsy-listing-image-tile" +
                      (selected?.sha256 === image.sha256 && selected?.packId === image.packId ? " is-selected" : "")}
                    key={image.packId + "-" + image.slot}
                    aria-label={"Xem ảnh " + image.slot + " / " + group.garmentCode}
                    aria-pressed={selected?.packId === image.packId && selected?.slot === image.slot}
                    onClick={() => setSelected(image)}>
                    <img src={image.thumbnailUrl} alt="" loading="lazy" />
                    <span>Ảnh {String(image.slot).padStart(2, "0")}</span>
                  </button>)}
                </div>
              </section>)}
            </>}
          </div>}
          <footer className="etsy-listing-image-foot">
            Chỉ đếm ảnh PNG đã lưu và đồng bộ thành công; không tính ảnh Etsy gốc hoặc slot chờ.
          </footer>
        </section>
      </div>, document.body
    )}
  </>;
});
