import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Listing } from "./api";

export function DesignCell({ item }: { item: Listing }) {
  const designs = Array.isArray(item.designs) ? item.designs : [];
  const [active, setActive] = useState<number | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (active === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActive(null);
      if (event.key === "ArrowRight" && designs.length > 1) {
        event.preventDefault();
        setActive(index => index === null ? null : (index + 1) % designs.length);
      }
      if (event.key === "ArrowLeft" && designs.length > 1) {
        event.preventDefault();
        setActive(index => index === null ? null : (index - 1 + designs.length) % designs.length);
      }
    };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [active, designs.length]);

  // No job, QA or waiting-state text belongs in the Picked Design cell.
  if (designs.length === 0) {
    return <span className="etsy-design-empty" aria-label="Chưa có ảnh thiết kế" />;
  }

  const selected = active === null ? 0 : Math.min(active, designs.length - 1);
  const preview = designs[selected];

  return <>
    <div className="etsy-design-thumbnails"
      aria-label={`Ảnh thiết kế sản phẩm ${item.listingKey}`}>
      {designs.slice(0, 3).map((design, index) => (
        <button key={design.job_id + ":" + design.id} type="button"
          className="etsy-design-thumb"
          onClick={() => setActive(index)}
          aria-label={`Xem thiết kế ${index + 1} trong ${designs.length} ảnh`}>
          <img loading="lazy" decoding="async" src={design.thumbnail_url} alt="" />
        </button>
      ))}
    </div>

    {active !== null && createPortal(
      <div className="etsy-design-gallery-backdrop"
        onMouseDown={event => {
          if (event.currentTarget === event.target) setActive(null);
        }}>
        <section role="dialog" aria-modal="true"
          aria-label={`Ảnh thiết kế sản phẩm ${item.listingKey}`}
          className="etsy-design-gallery">
          <button ref={closeRef} className="etsy-design-gallery-close" type="button"
            onClick={() => setActive(null)} aria-label="Đóng">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 5L19 19M19 5L5 19" />
            </svg>
          </button>
          <div className="etsy-design-gallery-main">
            <div className="etsy-design-gallery-stage">
              <img key={preview.id} src={preview.original_url}
                alt={`Thiết kế ${selected + 1}`} decoding="async" />
            </div>
            <aside aria-label="Danh sách ảnh thiết kế">
              <div className="etsy-design-gallery-list">
                {designs.map((design, index) => (
                  <button key={design.job_id + ":" + design.id} type="button"
                    className={index === selected ? "active" : ""}
                    onClick={() => setActive(index)}
                    aria-label={`Xem thiết kế ${index + 1}`}
                    aria-pressed={index === selected}>
                    <img loading="lazy" decoding="async"
                      src={design.thumbnail_url} alt="" />
                  </button>
                ))}
              </div>
            </aside>
          </div>
        </section>
      </div>, document.body
    )}
  </>;
}
