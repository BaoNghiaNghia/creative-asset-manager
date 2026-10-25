import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./OutputPhotoGalleryModal.css";
import { DeferredImage } from "./DeferredImage";
import { stage2ThumbnailUrl } from "./stage2Media";

export type PhotoOutputItem = {
  key: string;
  label: string;
  src: string | null;
  status: "pending" | "queued" | "running" | "completed" | "failed";
};

export type OutputPhotoGalleryModalProps = {
  title: string;
  items: PhotoOutputItem[];
  initialIndex?: number;
  onClose: () => void;
  onOpenVersions?: (item: PhotoOutputItem) => void;
  onRetry?: (item: PhotoOutputItem) => void;
  retryDisabled?: (item: PhotoOutputItem) => boolean;
};

const stateName: Record<PhotoOutputItem["status"], string> = {
  pending: "Not generated",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

function OutputPlaceholder({ status }: { status: PhotoOutputItem["status"] }) {
  return <div className={"rrugc-photo-gallery-placeholder is-" + status}>
    <svg viewBox="0 0 48 48" width="56" height="56" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 25c.8-9 5.8-14.5 14-14.5S37.2 16 38 25" />
      <path d="M7 25h34c3 0 4 1.5 4 4-5 4-12.3 6.1-21 6.1S8 33 3 29c0-2.5 1-4 4-4Z" />
      <path d="M24 11v14" />
    </svg>
    <strong>{stateName[status]}</strong>
    <span>{status === "failed" ? "This image could not be generated." :
      status === "running" ? "Image generation is in progress." :
        status === "queued" ? "Waiting for image generation." : "No output has been generated for this slot yet."}</span>
  </div>;
}

export function OutputPhotoGalleryModal({
  title, items, initialIndex = 0, onClose, onOpenVersions, onRetry, retryDisabled,
}: OutputPhotoGalleryModalProps) {
  const titleId = useId();
  const [index, setIndex] = useState(() => Math.max(0, Math.min(initialIndex, items.length - 1)));
  const closeButton = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const activeIndex = Math.max(0, Math.min(index, items.length - 1));
  const current = items[activeIndex];
  const completed = items.filter(item => item.status === "completed" && Boolean(item.src)).length;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      } else if (event.key === "ArrowRight" && items.length > 1) {
        event.preventDefault();
        setIndex(value => Math.min(value + 1, items.length - 1));
      } else if (event.key === "ArrowLeft" && items.length > 1) {
        event.preventDefault();
        setIndex(value => Math.max(value - 1, 0));
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [items.length]);

  if (!current) return null;
  const hasImage = current.status === "completed" && Boolean(current.src);
  const hasGallery = items.length > 1;

  return createPortal(<div className="rrugc-photo-gallery-overlay"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={"rrugc-photo-gallery" + (hasGallery ? " has-thumbnails" : " is-single")}
      role="dialog" aria-modal="true" aria-labelledby={titleId} data-photo-gallery={hasGallery ? "multi" : "single"}>
      <header className="rrugc-photo-gallery-header">
        <div className="rrugc-photo-gallery-heading">
          <span className="rrugc-photo-gallery-eyebrow">OUTPUT GALLERY</span>
          <h2 id={titleId}>{title}</h2>
          <p>{completed} of {items.length} {items.length === 1 ? "image" : "images"} generated</p>
        </div>
        <button type="button" className="rrugc-photo-gallery-close" ref={closeButton}
          onClick={onClose} aria-label="Close output gallery" title="Close (Esc)">×</button>
      </header>
      <div className="rrugc-photo-gallery-content">
        <div className="rrugc-photo-gallery-preview">
          <div className="rrugc-photo-gallery-preview-top">
            <span className="rrugc-photo-gallery-counter">{String(activeIndex + 1).padStart(2, "0")} / {String(items.length).padStart(2, "0")}</span>
            <strong>{current.label}</strong>
            <span className={"rrugc-photo-gallery-status is-" + current.status}>{stateName[current.status]}</span>
          </div>
          <div className="rrugc-photo-gallery-stage">
            {hasImage ? <DeferredImage key={current.key + current.src} src={current.src!} rootMargin="800px"
              alt={current.label} decoding="async" loading="eager" /> : <OutputPlaceholder status={current.status} />}
          </div>
          <div className="rrugc-photo-gallery-preview-footer">
            <div className="rrugc-photo-gallery-nav">
              {hasGallery && <>
                <button type="button" onClick={() => setIndex(value => Math.max(0, value - 1))}
                  aria-label="Previous output image" disabled={activeIndex === 0}>‹ <span>Previous</span></button>
                <button type="button" onClick={() => setIndex(value => Math.min(items.length - 1, value + 1))}
                  aria-label="Next output image" disabled={activeIndex === items.length - 1}><span>Next</span> ›</button>
              </>}
            </div>
            <div className="rrugc-photo-gallery-actions">
              {hasImage && <a href={current.src!} target="_blank" rel="noopener noreferrer"
                className="rrugc-photo-gallery-secondary-action">Open original ↗</a>}
              {hasImage && onOpenVersions && <button type="button" onClick={() => onOpenVersions(current)}>
                View versions</button>}
              {current.status === "failed" && onRetry && <button type="button" disabled={retryDisabled?.(current) || false}
                onClick={() => onRetry(current)}>↻ Retry image</button>}
            </div>
          </div>
        </div>
        {hasGallery && <aside className="rrugc-photo-gallery-sidebar" aria-label="All output slots">
          <div className="rrugc-photo-gallery-sidebar-heading">
            <strong>All {items.length} outputs</strong>
            <small>Select any slot, including those not yet generated</small>
          </div>
          <div className="rrugc-photo-gallery-thumbnails" role="group" aria-label="Output image thumbnails">
            {items.map((item, position) => {
              const ready = item.status === "completed" && Boolean(item.src);
              return <button type="button" key={item.key} onClick={() => setIndex(position)}
                aria-label={"View output " + (position + 1) + ": " + item.label + " · " + stateName[item.status]}
                aria-pressed={position === activeIndex}
                className={"rrugc-photo-gallery-thumb" + (position === activeIndex ? " is-selected" : "")}>
                <span className="rrugc-photo-gallery-thumb-media">
                  {ready ? <DeferredImage src={stage2ThumbnailUrl(item.src!, 128)} alt="" rootMargin="220px" /> : <span className={"rrugc-photo-gallery-thumb-empty is-" + item.status} aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
                      <path d="M5.2 13.1c.5-4.5 3.2-7.2 6.8-7.2s6.3 2.7 6.8 7.2" />
                      <path d="M4.1 13.1h14.7c1.4 0 2.4.5 2.8 1.4-2.4 1.7-5.6 2.5-9.6 2.5-4 0-7.2-.8-9.6-2.5.3-.9.9-1.4 1.7-1.4Z" />
                    </svg>
                  </span>}
                  <b>{String(position + 1).padStart(2, "0")}</b>
                </span>
                <span className="rrugc-photo-gallery-thumb-title" title={item.label}>{item.label}</span>
                <small className={"is-" + item.status}>{stateName[item.status]}</small>
              </button>;
            })}
          </div>
        </aside>}
      </div>
    </section>
  </div>, document.body);
}
