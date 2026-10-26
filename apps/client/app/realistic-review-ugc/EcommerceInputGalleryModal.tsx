import { useEffect } from "react";
import { createPortal } from "react-dom";
import { StableMasonryGrid } from "./StableMasonryGrid";
import { DeferredImage } from "./DeferredImage";
import { publicHatStockUrl, stage2OriginalUrl } from "./stage2Media";

export type InputPreviewItem = { key: string; label: string; src: string };

export function EcommerceInputGalleryModal({ items, onClose }: { items: InputPreviewItem[]; onClose: () => void }) {
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", keydown);
    return () => document.removeEventListener("keydown", keydown);
  }, [onClose]);
  const cards = (item: InputPreviewItem) => <figure className="rrugc-ecommerce-lightbox-card">
    <DeferredImage src={publicHatStockUrl(stage2OriginalUrl(item.src))} rootMargin="350px" alt={item.label} />
    <figcaption>{item.label}</figcaption>
  </figure>;
  return createPortal(<div className="rrugc-ecommerce-lightbox-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section role="dialog" aria-modal="true" aria-label="Ecommerce input and output image preview" className="rrugc-ecommerce-lightbox">
      <header><strong>{items.length > 1 ? "Image gallery" : items[0]?.label} · {items.length} {items.length === 1 ? "image" : "images"}</strong>
        <button type="button" onClick={onClose} aria-label="Close image preview">×</button></header>
      {items.length > 1
        ? <StableMasonryGrid items={items} getKey={item => item.key} renderItem={cards} maxColumns={4} minColumnWidth={220} ariaLabel="Ecommerce input masonry gallery" />
        : items[0] ? <div className="rrugc-ecommerce-lightbox-single">{cards(items[0])}</div> : null}
    </section>
  </div>, document.body);
}
