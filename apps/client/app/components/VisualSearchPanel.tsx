import { useEffect, useRef, useState } from "react";
import type { VisualCrop } from "../hooks/useVisualSearch";
import type { Asset } from "../types";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";

type Reference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string } | null;
type Props = { reference: Reference; loading: boolean; error: string; refinement: string; onRefinementChange: (value: string) => void; onUpload: (file: File, crop?: VisualCrop) => void; onApplyCrop: (crop: VisualCrop) => void; onRetry: (crop?: VisualCrop, text?: string) => void; onClose: () => void; };
const fullCrop: VisualCrop = { x: 0, y: 0, width: 1, height: 1 };

export function isHeicReference(asset: Pick<Asset, "mime_type" | "name">): boolean {
  return /image\/(heic|heif)/i.test(asset.mime_type) || asset.name.toLowerCase().endsWith(".heic") || asset.name.toLowerCase().endsWith(".heif");
}

export function visualReferencePreviewUrl(reference: Reference): string | null {
  if (!reference) return null;
  if (reference.kind === "upload") return reference.previewUrl;
  if (isHeicReference(reference.asset)) return reference.asset.thumbnail_url || explorerAssetUrl(reference.asset, "thumbnail");
  return assetPreviewUrl(reference.asset);
}

export function VisualSearchPanel({ reference, loading, error, refinement, onRefinementChange, onUpload, onApplyCrop, onRetry, onClose }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [crop, setCrop] = useState<VisualCrop>(fullCrop);
  useEffect(() => setCrop(fullCrop), [reference?.kind, reference?.kind === "asset" ? reference.asset.internal_asset_id : reference?.kind === "upload" ? reference.file.name : ""]);
  const preview = visualReferencePreviewUrl(reference);
  const update = (key: keyof VisualCrop, raw: number) => setCrop(current => {
    const next = { ...current, [key]: raw };
    if (key === "x") next.width = Math.min(next.width, 1 - next.x);
    if (key === "y") next.height = Math.min(next.height, 1 - next.y);
    return next;
  });
  return <section className="visual-search-panel" aria-label="Visual search">
    <header><div><small>VISUAL SEARCH</small><h2>Find similar images</h2></div><button type="button" onClick={onClose} aria-label="Close visual search">×</button></header>
    {!reference && <div className="visual-search-empty"><p>Choose an existing image or upload a reference image. Your upload is only used for this search.</p><button type="button" className="visual-primary" onClick={() => inputRef.current?.click()}>Upload reference image</button></div>}
    {reference && <div className="visual-search-workspace">
      <div className="visual-reference"><div className="visual-reference-preview">{preview && <img src={preview} alt="" />}<span className="visual-crop-rectangle" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.width * 100}%`, height: `${crop.height * 100}%` }} aria-hidden="true" /></div><p>{reference.kind === "asset" ? reference.asset.name : reference.file.name}</p></div>
      <fieldset className="visual-crop-controls"><legend>Crop region</legend>{(["x", "y", "width", "height"] as const).map(key => <label key={key}><span>{key === "x" ? "Left" : key === "y" ? "Top" : key === "width" ? "Width" : "Height"}</span><input type="range" min="0" max={key === "width" ? 1 - crop.x : key === "height" ? 1 - crop.y : 0.99} step="0.01" value={crop[key]} onChange={event => update(key, Number(event.target.value))} /></label>)}<div className="visual-crop-actions"><button type="button" onClick={() => onApplyCrop(crop)} disabled={loading}>Search this crop</button><button type="button" onClick={() => { setCrop(fullCrop); onRetry(); }} disabled={loading}>Use full image</button></div></fieldset>
      <label className="visual-refinement"><span>Refine results</span><input value={refinement} maxLength={500} placeholder="e.g. same garment but outdoor" onChange={event => onRefinementChange(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onRetry(crop, refinement); } }} /><button type="button" onClick={() => onRetry(crop, refinement)} disabled={loading}>Apply</button></label>
    </div>}
    {error && <div className="visual-search-error" role="alert"><span>{error}</span><button type="button" onClick={() => onRetry(crop)} disabled={loading}>Retry</button></div>}
    {loading && <p className="visual-search-loading" role="status">Finding similar images…</p>}
    <input ref={inputRef} type="file" accept="image/*" hidden onChange={event => { const file = event.currentTarget.files?.[0]; if (file) onUpload(file); event.currentTarget.value = ""; }} />
  </section>;
}
