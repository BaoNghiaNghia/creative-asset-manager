import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { VisualCrop, VisualSearchScope } from "../hooks/useVisualSearch";
import type { Asset } from "../types";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";

type Reference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string } | null;
type Props = { scope: VisualSearchScope | null; canSearchAllResources: boolean; onScopeChange: (scope: VisualSearchScope) => void; hasCurrentSource: boolean; hasCurrentFolder: boolean; reference: Reference; loading: boolean; error: string; refinement: string; onRefinementChange: (value: string) => void; onUpload: (file: File, crop?: VisualCrop) => void; onApplyCrop: (crop: VisualCrop) => void; onRetry: (crop?: VisualCrop, text?: string) => void; onClose: () => void; };
type DragMode = "move" | "nw" | "ne" | "sw" | "se";
type DragState = { mode: DragMode; start: { x: number; y: number }; crop: VisualCrop };
const fullCrop: VisualCrop = { x: 0, y: 0, width: 1, height: 1 };
const MIN_CROP = 0.1;

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

export function VisualSearchPanel({ scope, reference, loading, error, onUpload, onApplyCrop, onRetry, onClose }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [crop, setCrop] = useState<VisualCrop>(fullCrop);
  const cropRef = useRef(crop);
  const dragRef = useRef<DragState | null>(null);
  useEffect(() => { cropRef.current = crop; }, [crop]);
  useEffect(() => { cropRef.current = fullCrop; setCrop(fullCrop); }, [reference?.kind, reference?.kind === "asset" ? reference.asset.internal_asset_id : reference?.kind === "upload" ? reference.file.name : ""]);
  const preview = visualReferencePreviewUrl(reference);
  const begin = (event: ReactPointerEvent<HTMLElement>, mode: DragMode) => {
    if (loading) return;
    event.preventDefault();
    event.stopPropagation();
    const stage = stageRef.current;
    if (!stage) return;
    stage.setPointerCapture(event.pointerId);
    dragRef.current = { mode, start: point(event, stage), crop: cropRef.current };
  };
  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = dragRef.current;
    if (!active) return;
    const stage = stageRef.current;
    if (!stage) return;
    const current = point(event, stage), dx = current.x - active.start.x, dy = current.y - active.start.y;
    const initial = active.crop;
    let left = initial.x, right = initial.x + initial.width, top = initial.y, bottom = initial.y + initial.height;
    if (active.mode === "move") {
      left = clamp(initial.x + dx, 0, 1 - initial.width); right = left + initial.width;
      top = clamp(initial.y + dy, 0, 1 - initial.height); bottom = top + initial.height;
    } else {
      if (active.mode.includes("w")) left = clamp(initial.x + dx, 0, right - MIN_CROP);
      if (active.mode.includes("e")) right = clamp(initial.x + initial.width + dx, left + MIN_CROP, 1);
      if (active.mode.includes("n")) top = clamp(initial.y + dy, 0, bottom - MIN_CROP);
      if (active.mode.includes("s")) bottom = clamp(initial.y + initial.height + dy, top + MIN_CROP, 1);
    }
    const next = { x: left, y: top, width: right - left, height: bottom - top };
    cropRef.current = next; setCrop(next);
  };
  const finish = () => {
    const active = dragRef.current;
    if (!active) return;
    dragRef.current = null;
    if (scope && !sameCrop(active.crop, cropRef.current)) onApplyCrop(cropRef.current);
  };
  return <section className="visual-search-panel visual-search-panel-lens visual-search-panel-direct" aria-label="Visual search">
    <header><div><small>VISUAL SEARCH</small><h2>Find similar</h2></div><button type="button" className="visual-search-close" onClick={onClose} aria-label="Close visual search" title="Close">×</button></header>
    {!reference && <div className="visual-search-empty visual-lens-empty"><div><b>Search with an image</b><p>Choose an image from your library or upload one.</p></div><button type="button" className="visual-primary" onClick={() => inputRef.current?.click()} disabled={!scope}>Upload image</button></div>}
    {reference && <div className="visual-direct-workspace">
      <div ref={stageRef} className="visual-direct-stage" onPointerMove={move} onPointerUp={finish} onPointerCancel={finish} onDoubleClick={() => { cropRef.current = fullCrop; setCrop(fullCrop); onRetry(); }}>
        <img src={preview || ""} alt="" draggable={false} />
        <button type="button" className="visual-direct-change" onClick={() => inputRef.current?.click()} aria-label="Change image" title="Change image">×</button>
        <div className="visual-direct-crop" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.width * 100}%`, height: `${crop.height * 100}%` }} onPointerDown={event => begin(event, "move")} role="presentation">
          <i className="visual-direct-handle nw" onPointerDown={event => begin(event, "nw")} /><i className="visual-direct-handle ne" onPointerDown={event => begin(event, "ne")} /><i className="visual-direct-handle sw" onPointerDown={event => begin(event, "sw")} /><i className="visual-direct-handle se" onPointerDown={event => begin(event, "se")} />
        </div>
      </div>
      <div className="visual-direct-caption"><small>{loading ? "Searching…" : "Drag the frame to select an area · Double-click to use the full image"}</small></div>
      {!scope && <p className="visual-search-context" role="status">Choose an authorized source or folder before searching.</p>}
    </div>}
    {error && <div className="visual-search-error" role="alert"><span>{error}</span><button type="button" onClick={() => onRetry(crop)} disabled={loading}>Retry</button></div>}
    <input ref={inputRef} type="file" accept="image/*" hidden onChange={event => { const file = event.currentTarget.files?.[0]; if (file) onUpload(file); event.currentTarget.value = ""; }} />
  </section>;
}
