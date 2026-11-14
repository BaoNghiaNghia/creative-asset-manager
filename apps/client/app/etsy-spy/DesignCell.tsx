import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Listing } from "./api";
import { EtsyDeferredImage } from "./EtsyDeferredImage";

const DESIGN_BACKGROUNDS = [
  { id: "transparent", label: "Nền caro (mặc định)" },
  { id: "white", label: "Trắng" },
  { id: "cream", label: "Kem" },
  { id: "gray", label: "Xám nhạt" },
  { id: "black", label: "Đen" },
  { id: "navy", label: "Xanh navy" },
  { id: "sage", label: "Xanh lá nhạt" },
  { id: "pink", label: "Hồng nhạt" },
] as const;
type DesignBackground = (typeof DESIGN_BACKGROUNDS)[number]["id"];


const POSITION_LABELS: Record<string, string> = {
  front: "Mặt trước",
  back: "Mặt sau",
  left_sleeve: "Tay trái",
  right_sleeve: "Tay phải",
  sleeve: "Ống tay",
  chest: "Ngực áo",
  left_chest: "Ngực trái",
  right_chest: "Ngực phải",
  pocket: "Túi áo",
  hood: "Mũ áo",
};
const POSITION_ORDER = [
  "front", "back", "left_chest", "right_chest", "chest",
  "left_sleeve", "right_sleeve", "sleeve", "pocket", "hood",
] as const;

function positionKey(value: string) {
  return String(value || "unknown").trim().toLowerCase().replace(/[\s-]+/g, "_");
}
function positionLabel(key: string) {
  return POSITION_LABELS[key] ?? key.replace(/_/g, " ").slice(0, 36);
}
function variantLabel(value: number, ordinal: number) {
  return Number.isInteger(value) && value > 0 ? `Mẫu ${value}` : `Mẫu ${ordinal}`;
}

export function DesignCell({ item, onPreviewStart }: {
  item: Listing; onPreviewStart?: () => void;
}) {
  const designs = Array.isArray(item.designs) ? item.designs : [];
  const [active, setActive] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [failedOriginalId, setFailedOriginalId] = useState<string | null>(null);
  const [background, setBackground] = useState<DesignBackground>("transparent");
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
  const grouped = new Map<string, {
    key: string; label: string;
    entries: { design: (typeof designs)[number]; index: number }[];
  }>();
  designs.forEach((design, index) => {
    const key = positionKey(design.position);
    const existing = grouped.get(key);
    if (existing) existing.entries.push({design, index});
    else grouped.set(key, {key, label: positionLabel(key), entries: [{design, index}]});
  });
  const positions = Array.from(grouped.values()).sort((a, b) => {
    const aOrder = POSITION_ORDER.indexOf(a.key as (typeof POSITION_ORDER)[number]);
    const bOrder = POSITION_ORDER.indexOf(b.key as (typeof POSITION_ORDER)[number]);
    return (aOrder < 0 ? POSITION_ORDER.length : aOrder) -
      (bOrder < 0 ? POSITION_ORDER.length : bOrder);
  });
  const selectedPosition = grouped.get(positionKey(preview.position)) ?? positions[0];
  const selectedOrdinal = selectedPosition.entries.findIndex(entry => entry.index === selected) + 1;
  const selectedVariant = variantLabel(preview.variant, selectedOrdinal);
  const goToDesign = (delta: number) => {
    setActive(index => index === null ? null : (index + delta + designs.length) % designs.length);
  };

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
            setBackground("transparent");
            setActive(index);
          }}
          aria-label={`Xem thiết kế ${index + 1} trong ${designs.length} ảnh`}>
          <EtsyDeferredImage src={design.thumbnail_url} alt="" />
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
          className="etsy-design-gallery" data-preview-background={background}>
          <header className="etsy-design-gallery-header">
            <div className="etsy-design-gallery-heading">
              <span className="etsy-design-gallery-eyebrow">DESIGN PREVIEW</span>
              <h2>Thiết kế theo vị trí in</h2>
              <p title={item.pageTitle}>{item.pageTitle}</p>
            </div>
            <div className="etsy-design-gallery-header-meta" aria-label="Số lượng ảnh">
              <strong>{designs.length}</strong> thiết kế
              <span aria-hidden="true">·</span>
              <strong>{positions.length}</strong> vị trí
            </div>
            <button ref={closeRef} className="etsy-design-gallery-close" type="button"
              onClick={() => setActive(null)} aria-label="Đóng">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 5L19 19M19 5L5 19" />
              </svg>
            </button>
          </header>
          <div className="etsy-design-gallery-main">
            <div className="etsy-design-gallery-viewer">
              <div className="etsy-design-gallery-stage" aria-label="Ảnh thiết kế xem trước">
                <span className="etsy-design-gallery-stage-chip">
                  {selectedPosition.label} <span aria-hidden="true">/</span> {selectedVariant}
                </span>
                <EtsyDeferredImage key={preview.id} eager src={preview.original_url}
                  alt={`Thiết kế ${selectedVariant} - ${selectedPosition.label}`} />
              </div>
              <footer className="etsy-design-gallery-viewer-footer">
                <div className="etsy-design-gallery-current" aria-live="polite">
                  <strong>{selectedPosition.label} · {selectedVariant}</strong>
                  <span>{selected + 1} / {designs.length} thiết kế</span>
                </div>
                <div className="etsy-design-gallery-navigation" aria-label="Chuyển ảnh">
                  <button type="button" aria-label="Thiết kế trước" title="Thiết kế trước (←)"
                    onClick={() => goToDesign(-1)} disabled={designs.length === 1}>
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M15 5L8 12L15 19" />
                    </svg>
                  </button>
                  <button type="button" aria-label="Thiết kế tiếp theo" title="Thiết kế tiếp theo (→)"
                    onClick={() => goToDesign(1)} disabled={designs.length === 1}>
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M9 5L16 12L9 19" />
                    </svg>
                  </button>
                </div>
              </footer>
            </div>
            <aside className="etsy-design-gallery-side" aria-label="Vị trí và ảnh thiết kế">
              <section className="etsy-design-gallery-side-section">
                <div className="etsy-design-gallery-section-heading">
                  <strong>Vị trí in</strong>
                  <span>{positions.length} vị trí</span>
                </div>
                <div className="etsy-design-gallery-position-list" role="group"
                  aria-label="Chọn vị trí in">
                  {positions.map(position => (
                    <button key={position.key} type="button"
                      className={position.key === selectedPosition.key ? "active" : ""}
                      onClick={() => setActive(position.entries[0].index)}
                      aria-pressed={position.key === selectedPosition.key}
                      aria-label={`${position.label}, ${position.entries.length} thiết kế`}>
                      <span>{position.label}</span>
                      <span className="etsy-design-gallery-position-count">
                        {position.entries.length}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              <section className="etsy-design-gallery-side-section etsy-design-gallery-variants">
                <div className="etsy-design-gallery-section-heading">
                  <strong>Thiết kế · {selectedPosition.label}</strong>
                  <span>{selectedPosition.entries.length} mẫu</span>
                </div>
                <div className="etsy-design-gallery-list" role="group"
                  aria-label={`Các thiết kế ${selectedPosition.label}`}>
                  {selectedPosition.entries.map((entry, ordinal) => (
                    <button key={entry.design.job_id + ":" + entry.design.id} type="button"
                      className={entry.index === selected ? "active" : ""}
                      onClick={() => setActive(entry.index)}
                      aria-label={`Xem thiết kế ${entry.index + 1}, ${selectedPosition.label}, ${variantLabel(entry.design.variant, ordinal + 1)}`}
                      aria-pressed={entry.index === selected}>
                      <span className="etsy-design-gallery-variant-artwork">
                        <EtsyDeferredImage src={entry.design.thumbnail_url} alt=""
                          width={88} height={88} />
                      </span>
                      <span className="etsy-design-gallery-variant-label">
                        {variantLabel(entry.design.variant, ordinal + 1)}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              <section className="etsy-design-gallery-side-section etsy-design-gallery-backgrounds">
                <div className="etsy-design-gallery-section-heading">
                  <strong>Nền xem thử</strong>
                  <span>{DESIGN_BACKGROUNDS.find(option => option.id === background)?.label}</span>
                </div>
                <div className="etsy-design-gallery-background-picker"
                  role="group" aria-label="Màu nền xem thiết kế">
                  {DESIGN_BACKGROUNDS.map(option => (
                    <button key={option.id} type="button"
                      className={`etsy-design-gallery-background-swatch etsy-design-gallery-background-${option.id}`}
                      title={option.label} aria-label={option.label}
                      aria-pressed={background === option.id}
                      onClick={() => setBackground(option.id)} />
                  ))}
                </div>
                <p className="etsy-design-gallery-background-note">
                  Chỉ đổi màu nền xem trước, không thay đổi file PNG.
                </p>
              </section>
            </aside>
          </div>
        </section>
      </div>, document.body
    )}
  </>;
}