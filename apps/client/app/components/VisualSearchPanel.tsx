import { useEffect, useId, useRef, useState, type PointerEvent as ReactPointerEvent, type DragEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type { VisualCrop, VisualSearchScope } from "../hooks/useVisualSearch";
import type { Asset } from "../types";
import type { VisualHistoryEntry } from "../hooks/useRecentVisualSearches";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";

type Reference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string } | null;
type Props = { scope: VisualSearchScope | null; canSearchAllResources: boolean; onScopeChange: (scope: VisualSearchScope) => void; hasCurrentSource: boolean; hasCurrentFolder: boolean; reference: Reference; loading: boolean; preparingUpload?: boolean; error: string; refinement: string; onRefinementChange: (value: string) => void; onUpload: (file: File, crop?: VisualCrop) => void; onApplyCrop: (crop: VisualCrop) => void; onRetry: (crop?: VisualCrop, text?: string) => void; onClose: () => void; onPreviewError?: (file: File, previewUrl: string) => void; showRecentImages?: boolean; recentImages?: VisualHistoryEntry[]; onChooseAsset?: (asset: Asset) => void; onChooseUpload?: (file: File) => void; };
type DragMode = "create" | "nw" | "ne" | "sw" | "se";
type DragState = { mode: DragMode; start: { x: number; y: number }; crop: VisualCrop; changed: boolean };
const fullCrop: VisualCrop = { x: 0, y: 0, width: 1, height: 1 };
const MIN_CROP = 0.1;
const DRAG_THRESHOLD = 0.005;
const IMAGE_EXTENSION = /\.(?:avif|bmp|gif|heic|heif|jfif|jpe?g|png|tiff?|webp)$/i;

export function isVisualSearchImageFile(file: Pick<File, "name" | "type">): boolean {
  const mimeType = file.type.trim().toLowerCase();
  if (mimeType.startsWith("image/")) return true;
  return (!mimeType || mimeType === "application/octet-stream") && IMAGE_EXTENSION.test(file.name);
}

export function isHeicReference(asset: Pick<Asset, "mime_type" | "name">): boolean {
  return /image\/(heic|heif)/i.test(asset.mime_type) || asset.name.toLowerCase().endsWith(".heic") || asset.name.toLowerCase().endsWith(".heif");
}
export function visualReferencePreviewUrl(reference: Reference): string | null {
  if (!reference) return null;
  if (reference.kind === "upload") return reference.previewUrl;
  if (isHeicReference(reference.asset)) return reference.asset.thumbnail_url || explorerAssetUrl(reference.asset, "thumbnail");
  return assetPreviewUrl(reference.asset);
}
function point(event: ReactPointerEvent<HTMLElement>, element: HTMLElement) {
  const box = element.getBoundingClientRect();
  return { x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)) };
}
function clamp(value: number, min: number, max: number) { return Math.max(min, Math.min(max, value)); }
function sameCrop(left: VisualCrop, right: VisualCrop) { return left.x === right.x && left.y === right.y && left.width === right.width && left.height === right.height; }

export function VisualSearchPanel({ scope, reference, loading, preparingUpload = false, error, onUpload, onApplyCrop, onRetry, onClose, onPreviewError, showRecentImages = false, recentImages = [], onChooseAsset, onChooseUpload }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const stageRef = useRef<HTMLDivElement>(null);
  const [crop, setCrop] = useState<VisualCrop>(fullCrop);
  const cropRef = useRef(crop);
  const dragRef = useRef<DragState | null>(null);
  const [recentExpanded, setRecentExpanded] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  useEffect(() => { cropRef.current = crop; }, [crop]);
  useEffect(() => {
    if (reference) return;
    const previousOverflow = document.body.style.overflow;
    const previousPaddingRight = document.body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    if (scrollbarWidth > 0) {
      const currentPadding = parseFloat(getComputedStyle(document.body).paddingRight) || 0;
      document.body.style.paddingRight = (currentPadding + scrollbarWidth) + "px";
    }
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.body.style.paddingRight = previousPaddingRight;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [reference, onClose]);
  useEffect(() => { cropRef.current = fullCrop; setCrop(fullCrop); }, [reference?.kind, reference?.kind === "asset" ? reference.asset.internal_asset_id : reference?.kind === "upload" ? reference.file.name : ""]);
  const preview = visualReferencePreviewUrl(reference);
  const uploadFile = (file: File | undefined) => {
    if (!file || !scope) return;
    if (!isVisualSearchImageFile(file)) {
      setSelectionError("Choose an image file such as JPG, PNG, WebP, HEIC, or AVIF.");
      return;
    }
    setSelectionError("");
    onUpload(file);
  };
  const handleDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files || []);
    uploadFile(files.find(isVisualSearchImageFile) || files[0]);
  };
  const openPicker = () => {
    const input = inputRef.current;
    if (!input || !scope) return;
    input.value = "";
    input.click();
  };
  const handleDropzoneKeyDown = (event: ReactKeyboardEvent<HTMLLabelElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    openPicker();
  };
  const beginNewCrop = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (loading || event.button !== 0) return;
    event.preventDefault();
    const stage = stageRef.current;
    if (!stage) return;
    const start = point(event, stage);
    stage.setPointerCapture(event.pointerId);
    dragRef.current = { mode: "create", start, crop: cropRef.current, changed: false };
  };
  const begin = (event: ReactPointerEvent<HTMLElement>, mode: DragMode) => {
    if (loading || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const stage = stageRef.current;
    if (!stage) return;
    stage.setPointerCapture(event.pointerId);
    dragRef.current = { mode, start: point(event, stage), crop: cropRef.current, changed: false };
  };
  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = dragRef.current;
    if (!active) return;
    const stage = stageRef.current;
    if (!stage) return;
    const current = point(event, stage), dx = current.x - active.start.x, dy = current.y - active.start.y;
    if (active.mode === "create" && Math.max(Math.abs(dx), Math.abs(dy)) < DRAG_THRESHOLD) return;
    const initial = active.crop;
    let left = initial.x, right = initial.x + initial.width, top = initial.y, bottom = initial.y + initial.height;
    if (active.mode === "create") {
      left = Math.min(active.start.x, current.x); right = Math.max(active.start.x, current.x);
      top = Math.min(active.start.y, current.y); bottom = Math.max(active.start.y, current.y);
      if (right - left < MIN_CROP) { right = Math.min(1, left + MIN_CROP); left = Math.max(0, right - MIN_CROP); }
      if (bottom - top < MIN_CROP) { bottom = Math.min(1, top + MIN_CROP); top = Math.max(0, bottom - MIN_CROP); }
    } else {
      if (active.mode.includes("w")) left = clamp(initial.x + dx, 0, right - MIN_CROP);
      if (active.mode.includes("e")) right = clamp(initial.x + initial.width + dx, left + MIN_CROP, 1);
      if (active.mode.includes("n")) top = clamp(initial.y + dy, 0, bottom - MIN_CROP);
      if (active.mode.includes("s")) bottom = clamp(initial.y + initial.height + dy, top + MIN_CROP, 1);
    }
    const next = { x: left, y: top, width: right - left, height: bottom - top };
    active.changed = active.changed || !sameCrop(active.crop, next);
    cropRef.current = next; setCrop(next);
  };
  const finish = () => {
    const active = dragRef.current;
    if (!active) return;
    dragRef.current = null;
    if (scope && active.changed && !sameCrop(active.crop, cropRef.current)) onApplyCrop(cropRef.current);
  };
  const picker = <input
    ref={inputRef}
    id={inputId}
    className="visual-search-file-input"
    type="file"
    accept="image/*,.jpg,.jpeg,.jfif,.png,.webp,.gif,.bmp,.avif,.heic,.heif,.tif,.tiff"
    tabIndex={-1}
    disabled={!scope}
    onClick={event => { event.currentTarget.value = ""; }}
    onChange={event => {
      const file = event.currentTarget.files?.[0];
      event.currentTarget.value = "";
      uploadFile(file);
    }}
  />;
  const displayedError = selectionError || error;
  if (!reference) return <div className="visual-search-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="visual-search-modal" role="dialog" aria-modal="true" aria-labelledby="visual-search-modal-title">
      <header className="visual-search-modal-header">
        <h2 id="visual-search-modal-title">Upload an image to search</h2>
        <button type="button" className="visual-search-modal-close" onClick={onClose} aria-label="Close image search">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14M19 5 5 19" /></svg>
        </button>
      </header>
      <label
        htmlFor={inputId}
        className={"visual-search-dropzone" + (!scope ? " is-disabled" : "")}
        role="button"
        tabIndex={scope ? 0 : -1}
        aria-disabled={!scope}
        onDragOver={event => event.preventDefault()}
        onDrop={handleDrop}
        onKeyDown={handleDropzoneKeyDown}
      >
        <span className="visual-search-upload-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 16V8m0 0-3 3m3-3 3 3" /></svg>
        </span>
        <span>Choose a file or drag and drop it here</span>
        {!scope && <small>Search is unavailable until your account permissions finish loading.</small>}
      </label>
      {displayedError && <div className="visual-search-error visual-search-modal-error" role="alert"><span>{displayedError}</span></div>}
      {showRecentImages && <section className="visual-search-recent" aria-label="Visual search history">
        <div className="visual-search-recent-heading">
          <strong className="visual-search-recent-title">
            <span>Your recent images</span>
          </strong>
          {recentImages.length > 8 && <button type="button" className="visual-search-view-all" onClick={() => setRecentExpanded(value => !value)} aria-expanded={recentExpanded}>{recentExpanded ? "Show less" : "View all"}</button>}
        </div>
        {recentImages.length > 0 ? <div className="visual-search-recent-strip">
          {(recentExpanded ? recentImages : recentImages.slice(0, 8)).map(item => <button type="button" key={item.key} className="visual-search-recent-item" onClick={() => item.kind === "asset" ? onChooseAsset?.(item.asset) : onChooseUpload?.(item.file)} aria-label={"Search using " + (item.kind === "asset" ? item.asset.name : item.file.name)} title={item.kind === "asset" ? item.asset.name : item.file.name}>
            <img src={item.previewUrl} alt="" loading="lazy" />
          </button>)}
        </div> : <p className="visual-search-recent-empty">Your previous visual searches will appear here.</p>}
      </section>}
      {picker}
    </section>
  </div>;
  return <section className="visual-search-upload-card visual-search-upload-card--reference" aria-label="Visual search">
    <div className="visual-direct-actions" aria-label="Visual search actions">
      <button type="button" className="visual-direct-action visual-direct-replace" onClick={openPicker} aria-label="Change image" title="Change image">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 6.5A2.5 2.5 0 0 1 7 4h6.5m6 6.5V17a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-6.5"/><path d="m5.5 16 3.8-4 3.2 3 2.4-2.4 3.6 3.4"/><circle cx="9" cy="8.5" r="1.3"/><path d="M15 4h5m0 0v5m0-5-6 6"/></svg>
      </button>
      <button type="button" className="visual-direct-action visual-direct-close" onClick={onClose} aria-label="Close visual search" title="Close visual search">×</button>
    </div>
    <div className="visual-direct-workspace">
      <div ref={stageRef} className="visual-direct-stage" onPointerDown={beginNewCrop} onPointerMove={move} onPointerUp={finish} onPointerCancel={finish} onDoubleClick={() => { cropRef.current = fullCrop; setCrop(fullCrop); onRetry(); }}>
        <img src={preview || ""} alt="" draggable={false} onError={() => { if (reference?.kind === "upload" && preview) onPreviewError?.(reference.file, preview); }} />
        <div className="visual-direct-crop" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.width * 100}%`, height: `${crop.height * 100}%` }} role="presentation">
          <i className="visual-direct-handle nw" onPointerDown={event => begin(event, "nw")} /><i className="visual-direct-handle ne" onPointerDown={event => begin(event, "ne")} /><i className="visual-direct-handle sw" onPointerDown={event => begin(event, "sw")} /><i className="visual-direct-handle se" onPointerDown={event => begin(event, "se")} />
        </div>
      </div>
      <div className="visual-direct-caption"><small>{preparingUpload ? "Optimizing large image for search…" : loading ? "Searching…" : "Drag anywhere on the image to crop · Drag a corner to resize · Double-click for full image"}</small></div>
      {!scope && <p className="visual-search-context" role="status">Choose an authorized source or folder before searching.</p>}
    </div>
    {displayedError && <div className="visual-search-error" role="alert"><span>{displayedError}</span><button type="button" onClick={() => onRetry(crop)} disabled={loading}>Retry</button></div>}
    {picker}
  </section>;
}
