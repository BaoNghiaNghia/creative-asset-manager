import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { listGenerationOutputVersions, type GenerationOutputVersion } from "./api";
import type { KeywordImageRow } from "./types";
import { OutputActivityLoader } from "./OutputActivityLoader";

/**
 * Stage 1 table output strip. Fetch only when this row enters the viewport;
 * keep all versions in a horizontal strip with thumbnails sized to their natural aspect ratio.
 */
export function KeywordImageOutputSlider({ row, onOpenVersion, pickedOverrides }: {
  row: KeywordImageRow;
  onOpenVersion: (version: number) => void;
  pickedOverrides?: Readonly<Record<number, boolean>>;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; scrollLeft: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const [visible, setVisible] = useState(false);
  const [images, setImages] = useState<GenerationOutputVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [scroll, setScroll] = useState({ left: 0, end: true });
  const [reload, setReload] = useState(0);
  const [imageRetry, setImageRetry] = useState(0);
  const [brokenVersions, setBrokenVersions] = useState<ReadonlySet<number>>(new Set());
  useEffect(() => {
    const retry = () => { setBrokenVersions(new Set()); setImageRetry(n => n + 1); };
    document.addEventListener("rrugc-media-storage-restored", retry);
    return () => document.removeEventListener("rrugc-media-storage-restored", retry);
  }, []);
  const hasOutput = Boolean(row.job_id && (row.saved_output_count > 0 || row.output_url));

  useEffect(() => {
    setVisible(false);
    setImages([]);
    setError(false);
    setBrokenVersions(new Set());
  }, [row.job_id]);

  useEffect(() => {
    if (!hasOutput) return;
    const node = rootRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "180px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [row.job_id, hasOutput]);

  useEffect(() => {
    if (!hasOutput || !visible || !row.job_id) return;
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    void listGenerationOutputVersions("stage1", row.job_id, controller.signal)
      .then(response => { if (!controller.signal.aborted) setImages(response.versions); })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [row.job_id, row.saved_output_count, hasOutput, visible, reload]);

  const updateScroll = () => {
    const node = viewportRef.current;
    if (!node) return;
    setScroll({ left: node.scrollLeft, end: node.scrollLeft + node.clientWidth >= node.scrollWidth - 2 });
  };
  useEffect(() => {
    // New versions, or a failed thumbnail fallback, can change the strip width.
    const frame = window.requestAnimationFrame(updateScroll);
    const node = viewportRef.current;
    const observer = node && typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(updateScroll) : null;
    if (node) observer?.observe(node);
    return () => { window.cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [images.length, brokenVersions.size]);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    const node = viewportRef.current;
    if (!node) return;
    drag.current = { x: event.clientX, scrollLeft: node.scrollLeft, moved: false };
    suppressClick.current = false;
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current || !(event.buttons & 1)) return;
    const node = viewportRef.current;
    if (!node) return;
    const dx = event.clientX - drag.current.x;
    if (Math.abs(dx) < 5 && !drag.current.moved) return;
    drag.current.moved = true;
    suppressClick.current = true;
    node.scrollLeft = drag.current.scrollLeft - dx;
    updateScroll();
    if (!node.hasPointerCapture(event.pointerId)) node.setPointerCapture(event.pointerId);
  };
  const onPointerEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    drag.current = null;
    if (viewportRef.current?.hasPointerCapture(event.pointerId)) {
      viewportRef.current.releasePointerCapture(event.pointerId);
    }
    // A captured pointer may not dispatch a click. Avoid blocking the next
    // real thumbnail click after dragging ends.
    window.setTimeout(() => { suppressClick.current = false; }, 0);
  };
  const moveByPage = (direction: number) => {
    viewportRef.current?.scrollBy({ left: direction * viewportRef.current.clientWidth, behavior: "smooth" });
  };

  const slides: GenerationOutputVersion[] = images.length > 0
    ? images
    : !loading && row.output_url
      ? [{ version: 1, url: row.output_url, created_at: "", width: null, height: null }]
      : [];
  const count = Math.max(row.saved_output_count, images.length);
  return <div className="rrugc-keyword-output" ref={rootRef} aria-label={`Generated images for ${row.keyword}`}>
    {slides.length > 0
      ? <div className="rrugc-keyword-output-gallery" role="group"
          aria-label={`Browse generated images for ${row.keyword}`}>
          <button type="button" className="rrugc-keyword-output-arrow is-prev"
            aria-label={`Previous images for ${row.keyword}`} title="Previous images"
            onClick={() => moveByPage(-1)} disabled={scroll.left < 2}>
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
          </button>
          <div ref={viewportRef} className="rrugc-keyword-output-track" role="group"
            aria-label={`Drag to browse ${count || slides.length} generated images for ${row.keyword}`}
            onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerEnd}
            onPointerCancel={onPointerEnd} onScroll={updateScroll}
            onDragStart={event => event.preventDefault()}
            onClickCapture={event => { if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; } }}
            onKeyDown={event => {
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                moveByPage(event.key === "ArrowRight" ? 1 : -1);
              }
            }} tabIndex={0}>
            {slides.map((image, index) => {
              const picked = pickedOverrides?.[image.version] ?? Boolean(image.selected_for_products_stage2);
              return <button type="button" key={image.version}
                className={"rrugc-keyword-output-image" + (picked ? " is-stage2-picked" : "")}
                onClick={() => onOpenVersion(image.version)}
                title={(image.output_name || `Image ${index + 1} · Version ${image.version}`) + (picked ? " · Picked for Products Stage 2" : "")}
                aria-label={`View generated image ${index + 1} of ${slides.length} for ${row.keyword}${picked ? " · Picked for Products Stage 2" : ""}`}
                draggable={false}>
                {brokenVersions.has(image.version)
                  ? <span className="rrugc-output-image-failed">Tạm không tải được ảnh</span>
                  : <img src={image.url + (image.url.includes("?") ? "&" : "?") + "thumbnail=true&size=256&artwork=stage1-source-alpha-v2" + (imageRetry ? "&retry=" + imageRetry : "")}
                    alt={`Generated image ${index + 1}`} loading="lazy" draggable={false}
                    onLoad={updateScroll}
                    onError={() => setBrokenVersions(current => new Set([...current, image.version]))} /> }
                {picked && <span className="rrugc-keyword-output-picked-mark" aria-hidden="true">✓</span>}
              </button>;
            })}
          </div>
          <button type="button" className="rrugc-keyword-output-arrow is-next"
            aria-label={`Next images for ${row.keyword}`} title="Next images"
            onClick={() => moveByPage(1)} disabled={scroll.end}>
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6" /></svg>
          </button>
        </div>
      : <div className="rrugc-keyword-output-placeholder">
          {error ? "Could not load images"
            : row.status === "running" || row.status === "queued"
              ? <OutputActivityLoader status={row.status} />
              : loading || hasOutput ? "Loading images…" : "No images yet"}
        </div>}
    {((row.status === "queued" || row.status === "running") && slides.length > 0 || error || brokenVersions.size > 0) &&
      <div className="rrugc-keyword-output-footer" aria-label="Output activity and reload controls">
        {(row.status === "queued" || row.status === "running") && slides.length > 0 &&
          <OutputActivityLoader compact status={row.status} />}
        {(error || brokenVersions.size > 0) && <button type="button" className="rrugc-keyword-output-retry"
          title="Reload images" aria-label={`Reload images for ${row.keyword}`} onClick={() => { setReload(current => current + 1); setBrokenVersions(new Set()); setImageRetry(n => n + 1); }}>↻</button>}
      </div>}
  </div>;
}