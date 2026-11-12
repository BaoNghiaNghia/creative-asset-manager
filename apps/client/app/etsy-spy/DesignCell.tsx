import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Listing } from "./api";

export function DesignCell({ item, onPreviewStart }: {
  item: Listing; onPreviewStart?: () => void;
}) {
  const designs = Array.isArray(item.designs) ? item.designs : [];
  const [active, setActive] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [failedOriginalId, setFailedOriginalId] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const galleryRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLButtonElement | null>(null);
  const galleryCountRef = useRef(designs.length);
  galleryCountRef.current = designs.length;
  const galleryOpen = active !== null && designs.length > 0;

  useEffect(() => {
    if (!galleryOpen) return;
    const previousOverflow = document.body.style.overflow;
    const returnFocus = returnFocusRef.current;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setActive(null);
      } else if (event.key === "Tab") {
        const buttons = Array.from(galleryRef.current?.querySelectorAll<HTMLButtonElement>(
          "button:not([disabled])",
        ) || []);
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        const focused = document.activeElement;
        const outside = !galleryRef.current?.contains(focused);
        if (first && last && ((event.shiftKey && (focused === first || outside)) ||
          (!event.shiftKey && (focused === last || outside)))) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      } else if (event.key === "ArrowRight" && galleryCountRef.current > 1) {
        event.preventDefault();
        setActive(index => index === null ? null : (index + 1) % galleryCountRef.current);
      } else if (event.key === "ArrowLeft" && galleryCountRef.current > 1) {
        event.preventDefault();
        setActive(index => index === null ? null :
          (index - 1 + galleryCountRef.current) % galleryCountRef.current);
      }
    };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, [galleryOpen]);

  useEffect(() => {
    if (designs.length === 0) setActive(null);
  }, [designs.length]);

  // Match the Etsy thumbnail preview: viewport-anchored, bottom-left, with
  // original Design PNG loaded only when hovered/focused. Touch opens gallery.
  useEffect(() => {
    if (hovered === null) return;
    const hide = () => setHovered(null);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [hovered]);

  const showHover = (index: number) => {
    onPreviewStart?.(); // hide the Etsy-row tooltip underneath the Design tooltip
    setFailedOriginalId(null);
    setHovered(index);
  };

  // No job, QA or waiting-state text belongs in the Picked Design cell.
  if (designs.length === 0) {
    return <span className="etsy-design-empty" aria-label="Chưa có ảnh thiết kế" />;
  }

  const selected = active === null ? 0 : Math.min(active, designs.length - 1);
  const preview = designs[selected];
  const hoveredDesign = hovered === null ? null : designs[hovered];

  return <>
    <div className="etsy-design-thumbnails"
      aria-label={`Ảnh thiết kế sản phẩm ${item.listingKey}`}>
      {designs.slice(0, 3).map((design, index) => (
        <button key={design.job_id + ":" + design.id} type="button"
          className="etsy-design-thumb"
          onPointerEnter={event => {
            if (event.pointerType !== "touch") showHover(index);
          }}
          onPointerLeave={() => setHovered(null)}
          onPointerCancel={() => setHovered(null)}
          onFocus={() => showHover(index)}
          onBlur={() => setHovered(null)}
          onClick={event => {
            returnFocusRef.current = event.currentTarget;
            setHovered(null);
            setActive(index);
          }}
          aria-label={`Xem thiết kế ${index + 1} trong ${designs.length} ảnh`}>
          <img loading="lazy" decoding="async" src={design.thumbnail_url} alt="" />
        </button>
      ))}
    </div>

    {hoveredDesign && active === null && createPortal(
      <div className="etsy-product-hover-preview etsy-design-hover-preview"
        role="tooltip" data-listing-key={item.listingKey}
        data-design-index={hovered}>
        <span className="etsy-product-preview-label">Ảnh Design {hovered! + 1}/{designs.length}</span>
        <img src={failedOriginalId === hoveredDesign.id
          ? hoveredDesign.thumbnail_url : hoveredDesign.original_url}
          alt={`Thiết kế ${hovered! + 1} của ${item.pageTitle}`}
          decoding="async"
          onError={() => {
            if (failedOriginalId !== hoveredDesign.id) setFailedOriginalId(hoveredDesign.id);
            else setHovered(null);
          }} />
        <span className="etsy-product-preview-title">
          <span className="etsy-product-preview-title-text">{item.pageTitle}</span>
        </span>
      </div>, document.body
    )}

    {active !== null && createPortal(
      <div className="etsy-design-gallery-backdrop"
        onMouseDown={event => {
          if (event.currentTarget === event.target) setActive(null);
        }}>
        <section ref={galleryRef} role="dialog" aria-modal="true"
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
