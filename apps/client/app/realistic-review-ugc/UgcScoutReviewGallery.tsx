import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { UgcScoutItem } from "./api";
import { DeferredImage } from "./DeferredImage";
import { stage2PinterestThumbnailUrl } from "./stage2Media";
import "./UgcScoutReviewGallery.css";

export type UgcScoutReviewLabel = "good" | "bad" | "ai" | "clear";
export type UgcPinnedShot = { slot: string; status: string };

type Props = {
  items: UgcScoutItem[];
  pinned: Readonly<Record<string, UgcPinnedShot>>;
  onReview: (item: UgcScoutItem, label: UgcScoutReviewLabel) => Promise<void>;
};

export function UgcScoutReviewGallery({ items, pinned, onReview }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const trigger = useRef<HTMLButtonElement | null>(null);
  const selectedIndex = items.findIndex(item => item.id === selectedId);
  const selected = selectedIndex >= 0 ? items[selectedIndex] : null;
  const close = () => { if (!busy) { setSelectedId(null); setError(""); } };
  const move = (delta: number) => {
    if (busy || selectedIndex < 0 || !items.length) return;
    setSelectedId(items[(selectedIndex + delta + items.length) % items.length].id);
    setError("");
  };

  useEffect(() => {
    if (!selectedId) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const keyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
      if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    };
    document.addEventListener("keydown", keyDown);
    return () => {
      document.removeEventListener("keydown", keyDown);
      document.body.style.overflow = previousOverflow;
    };
  });

  useEffect(() => {
    if (!selectedId && trigger.current?.isConnected) trigger.current.focus();
  }, [selectedId]);

  async function save(label: UgcScoutReviewLabel) {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      await onReview(selected, label);
      if (label === "bad" || label === "ai") {
        // The server hides negative references. Move to another eligible image.
        const next = items.filter(item => item.id !== selected.id);
        setSelectedId(next.length ? next[Math.min(selectedIndex, next.length - 1)].id : null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save feedback.");
    } finally {
      setBusy(false);
    }
  }

  return <>
    <div className="rrugc-ugc-review-grid" role="group" aria-label="Lifestyle reference images">
      {items.map((item, index) => {
        const used = pinned[item.id];
        return <button type="button" className={"rrugc-ugc-review-tile" + (used ? " is-pinned" : "")}
          key={item.id} aria-label={"Review lifestyle image " + (index + 1) + (used ? " · Picked for " + used.slot : "")}
          onClick={event => { trigger.current = event.currentTarget; setSelectedId(item.id); setError(""); }}>
          <DeferredImage src={stage2PinterestThumbnailUrl(item.image_url)} rootMargin="240px" alt={"Lifestyle reference " + (index + 1)} />
          {used && <span className="rrugc-ugc-review-picked" title={"Pinned for " + used.slot + " · " + used.status}>
            ✓ {used.slot} · {used.status === "completed" ? "Done" : used.status}
          </span>}
          {item.manual_label === "good" && <span className="rrugc-ugc-review-preferred">✓ Preferred</span>}
          {!used && item.manual_label !== "good" && <span className="rrugc-ugc-review-hover">Review ↗</span>}
        </button>;
      })}
    </div>
    {selected && typeof document !== "undefined" && createPortal(
      <div className="rrugc-ugc-review-backdrop" role="presentation"
        onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
        <section className="rrugc-ugc-review-dialog" role="dialog" aria-modal="true"
          aria-label={"Review lifestyle reference " + (selectedIndex + 1)}>
          <header>
            <div><small>REVIEW SCOUT · REFERENCE FEEDBACK</small>
              <strong>Image {selectedIndex + 1} / {items.length}</strong>
              {pinned[selected.id] && <span className="rrugc-ugc-review-modal-picked">
                ✓ Picked for {pinned[selected.id].slot} · {pinned[selected.id].status}
              </span>}</div>
            <button type="button" className="rrugc-ugc-review-close" aria-label="Close reference review" onClick={close} disabled={busy}>×</button>
          </header>
          <div className="rrugc-ugc-review-media">
            <button type="button" aria-label="Previous lifestyle reference" disabled={busy || items.length < 2} onClick={() => move(-1)}>‹</button>
            <img src={selected.image_url} alt={"Lifestyle Scout reference " + (selectedIndex + 1)} />
            <button type="button" aria-label="Next lifestyle reference" disabled={busy || items.length < 2} onClick={() => move(1)}>›</button>
          </div>
          <div className="rrugc-ugc-review-actions" role="group" aria-label="Save Scout learning feedback">
            <button type="button" className={"is-good" + (selected.manual_label === "good" ? " is-current" : "")}
              disabled={busy} aria-label="Pick good lifestyle reference" onClick={() => void save("good")}>✓ Pick</button>
            <button type="button" className="is-bad" disabled={busy}
              aria-label="Reject lifestyle reference" onClick={() => void save("bad")}>✕ Reject</button>
            <button type="button" className="is-ai" disabled={busy}
              aria-label="Mark lifestyle reference as AI-generated" onClick={() => void save("ai")}>AI</button>
            {selected.manual_label && <button type="button" className="is-clear" disabled={busy}
              aria-label="Clear lifestyle reference feedback" onClick={() => void save("clear")}>Clear</button>}
          </div>
          {error && <p role="alert" className="rrugc-ugc-review-error">{error}</p>}
          <footer>
            <span>{busy ? "Saving feedback…" : "✓ teaches Scout what to seek; ✕ and AI exclude similar references. Completed outputs stay unchanged."}</span>
            {selected.pin_url && <a href={selected.pin_url} target="_blank" rel="noopener noreferrer">Pinterest ↗</a>}
          </footer>
        </section>
      </div>, document.body,
    )}
  </>;
}
