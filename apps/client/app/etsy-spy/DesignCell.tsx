import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Listing, DesignAsset } from "./api";

const POSITION_LABELS: Record<string, string> = {
  front: "Mặt trước", back: "Mặt sau",
  left_chest: "Ngực trái", right_chest: "Ngực phải",
  left_sleeve: "Tay trái", right_sleeve: "Tay phải",
  neck: "Cổ áo", hem: "Gấu áo", other: "Khác",
  unknown: "Chưa xác định",
};

function positionName(value: string) {
  return POSITION_LABELS[value] || POSITION_LABELS.unknown;
}

export function DesignCell({ item }: { item: Listing }) {
  const designs = Array.isArray(item.designs) ? item.designs : [];
  const [active, setActive] = useState<number | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (active === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActive(null);
    };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);

  if (designs.length === 0) {
    return <div className="etsy-design-empty" aria-label="Chưa có design PNG">
      <span aria-hidden="true">◇</span>
      <small>{["pending", "processing", "retry"].includes(item.extraction?.status || "")
        ? "Đang chờ tách" : "Chưa có PNG"}</small>
    </div>;
  }
  const preview = designs[active === null ? 0 : active] || designs[0];
  return <>
    <div className="etsy-design-preview-strip"
      aria-label={designs.length + " design PNG cho sản phẩm " + item.listingKey}>
      <div className="etsy-design-thumbnails">
        {designs.slice(0, 3).map((design, index) =>
          <button type="button" key={design.job_id + ":" + design.id}
            className="etsy-design-thumb" onClick={() => setActive(index)}
            title={positionName(design.position) + " · bản " + design.variant + " · cần QA"}
            aria-label={"Xem design " + (index + 1) + " · " + positionName(design.position)}>
            <img loading="lazy" decoding="async" src={design.thumbnail_url}
              alt={"Design PNG " + positionName(design.position)} />
          </button>)}
        {designs.length > 3 && <button type="button" className="etsy-design-more"
          onClick={() => setActive(3)} aria-label={"Xem tất cả " + designs.length + " design"}>
          +{designs.length - 3}
        </button>}
      </div>
      <button className="etsy-design-open" type="button" onClick={() => setActive(0)}>
        {designs.length} design · Xem tất cả
      </button>
      <small className="etsy-design-review-label">PNG nháp · cần QA</small>
    </div>
    {active !== null && createPortal(
      <div className="etsy-design-gallery-backdrop"
        onMouseDown={event => { if (event.currentTarget === event.target) setActive(null); }}>
        <section role="dialog" aria-modal="true" aria-label={"Design sản phẩm " + item.listingKey}
          className="etsy-design-gallery">
          <header>
            <div><strong>Design · #{item.listingKey}</strong>
              <small>{item.pageTitle} · {designs.length} PNG</small></div>
            <button ref={closeRef} type="button" onClick={() => setActive(null)}
              aria-label="Đóng thư viện design">×</button>
          </header>
          <div className="etsy-design-gallery-main">
            <div className="etsy-design-gallery-stage">
              <img src={preview.original_url} alt={"PNG " + positionName(preview.position)}
                decoding="async" />
            </div>
            <aside>
              <strong>{positionName(preview.position)} · Bản {preview.variant}</strong>
              <small>Nguồn: {preview.method} · {preview.source}</small>
              <small>Trạng thái: Cần QA · Chưa xác minh quyền sử dụng</small>
              <small>Job: {preview.job_id}</small>
              <a href={preview.original_url} target="_blank" rel="noopener noreferrer">
                Mở PNG gốc
              </a>
              <div className="etsy-design-gallery-list">
                {designs.map((design, index) => <button
                  key={design.job_id + ":" + design.id} type="button"
                  className={index === active ? "active" : ""}
                  onClick={() => setActive(index)}
                  aria-pressed={index === active}
                  title={positionName(design.position) + " · v" + design.variant}>
                  <img loading="lazy" src={design.thumbnail_url}
                    alt={positionName(design.position)} decoding="async" />
                  <span>{positionName(design.position)} <small>v{design.variant}</small></span>
                </button>)}
              </div>
            </aside>
          </div>
          <footer>Chỉ dùng để kiểm duyệt. Chưa xác minh độ nét, chữ, hình và quyền thương mại.</footer>
        </section>
      </div>, document.body)}
  </>;
}
