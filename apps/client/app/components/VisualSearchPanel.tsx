import { useEffect, useRef, useState } from "react";
import type { VisualCrop, VisualSearchScope } from "../hooks/useVisualSearch";
import type { Asset } from "../types";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";

type Reference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string } | null;
type Props = { scope: VisualSearchScope | null; canSearchAllResources: boolean; onScopeChange: (scope: VisualSearchScope) => void; hasCurrentSource: boolean; hasCurrentFolder: boolean; reference: Reference; loading: boolean; error: string; refinement: string; onRefinementChange: (value: string) => void; onUpload: (file: File, crop?: VisualCrop) => void; onApplyCrop: (crop: VisualCrop) => void; onRetry: (crop?: VisualCrop, text?: string) => void; onClose: () => void; };
const fullCrop: VisualCrop = { x: 0, y: 0, width: 1, height: 1 };
const scopeOptions = [["all", "All images"], ["source", "This source"], ["folder", "This folder"]] as const;

export function isHeicReference(asset: Pick<Asset, "mime_type" | "name">): boolean {
  return /image\/(heic|heif)/i.test(asset.mime_type) || asset.name.toLowerCase().endsWith(".heic") || asset.name.toLowerCase().endsWith(".heif");
}
export function visualReferencePreviewUrl(reference: Reference): string | null {
  if (!reference) return null;
  if (reference.kind === "upload") return reference.previewUrl;
  if (isHeicReference(reference.asset)) return reference.asset.thumbnail_url || explorerAssetUrl(reference.asset, "thumbnail");
  return assetPreviewUrl(reference.asset);
}

export function VisualSearchPanel({ scope, canSearchAllResources, onScopeChange, hasCurrentSource, hasCurrentFolder, reference, loading, error, refinement, onRefinementChange, onUpload, onApplyCrop, onRetry, onClose }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [crop, setCrop] = useState<VisualCrop>(fullCrop);
  const [refineOpen, setRefineOpen] = useState(false);
  useEffect(() => { setCrop(fullCrop); setRefineOpen(false); }, [reference?.kind, reference?.kind === "asset" ? reference.asset.internal_asset_id : reference?.kind === "upload" ? reference.file.name : ""]);
  const preview = visualReferencePreviewUrl(reference);
  const update = (key: keyof VisualCrop, raw: number) => setCrop(current => {
    const next = { ...current, [key]: raw };
    if (key === "x") next.width = Math.min(next.width, 1 - next.x);
    if (key === "y") next.height = Math.min(next.height, 1 - next.y);
    return next;
  });
  const enabled = (value: VisualSearchScope) => value === "all" ? canSearchAllResources : value === "source" ? hasCurrentSource : hasCurrentFolder;
  return <section className="visual-search-panel visual-search-panel-lens" aria-label="Visual search">
    <header><div><small>VISUAL SEARCH</small><h2>Find similar</h2></div><button type="button" className="visual-search-close" onClick={onClose} aria-label="Close visual search" title="Close">×</button></header>
    {!reference && <div className="visual-search-empty visual-lens-empty"><div><b>Search with an image</b><p>Choose an image from your library or upload one.</p><label className="visual-lens-scope"><span>Search in</span><select value={scope || ""} onChange={event => { const value = event.target.value as VisualSearchScope; if (value) onScopeChange(value); }} aria-label="Search scope">{scopeOptions.map(([value, label]) => <option key={value} value={value} disabled={!enabled(value)}>{label}</option>)}</select></label></div><button type="button" className="visual-primary" onClick={() => inputRef.current?.click()} disabled={!scope}>Upload image</button></div>}
    {reference && <div className="visual-lens-main">
      <div className="visual-reference visual-lens-reference"><div className="visual-reference-preview">{preview && <img src={preview} alt="" />}{refineOpen && <span className="visual-crop-rectangle" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.width * 100}%`, height: `${crop.height * 100}%` }} aria-hidden="true" />}</div><p title={reference.kind === "asset" ? reference.asset.name : reference.file.name}>{reference.kind === "asset" ? reference.asset.name : reference.file.name}</p></div>
      <div className="visual-lens-actions">
        <label className="visual-lens-scope"><span>Search in</span><select value={scope || ""} onChange={event => { const value = event.target.value as VisualSearchScope; if (value) onScopeChange(value); }} aria-label="Search scope">{scopeOptions.map(([value, label]) => <option key={value} value={value} disabled={!enabled(value)}>{label}</option>)}</select></label>
        {!scope && <p className="visual-search-context" role="status">Choose an authorized source or folder before searching.</p>}
        <div className="visual-lens-primary-actions"><button type="button" className="visual-primary" onClick={() => { setCrop(fullCrop); onRetry(); }} disabled={loading || !scope}>Search similar</button><button type="button" className="visual-lens-secondary" onClick={() => inputRef.current?.click()} disabled={loading}>Change image</button></div>
        <button type="button" className="visual-lens-refine-toggle" onClick={() => setRefineOpen(open => !open)} aria-expanded={refineOpen}>Refine search <span aria-hidden="true">{refineOpen ? "−" : "+"}</span></button>
      </div>
    </div>}
    {reference && refineOpen && <div className="visual-lens-refine">
      <fieldset className="visual-crop-controls"><legend>Crop image</legend>{(["x", "y", "width", "height"] as const).map(key => <label key={key}><span>{key === "x" ? "Left" : key === "y" ? "Top" : key === "width" ? "Width" : "Height"}</span><input type="range" min="0" max={key === "width" ? 1 - crop.x : key === "height" ? 1 - crop.y : 0.99} step="0.01" value={crop[key]} onChange={event => update(key, Number(event.target.value))} /></label>)}<div className="visual-crop-actions"><button type="button" onClick={() => onApplyCrop(crop)} disabled={loading || !scope}>Search selection</button><button type="button" onClick={() => { setCrop(fullCrop); onRetry(); }} disabled={loading || !scope}>Use full image</button></div></fieldset>
      <label className="visual-refinement"><span>Describe what matters</span><input value={refinement} maxLength={500} placeholder="e.g. black formal dress" onChange={event => onRefinementChange(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onRetry(crop, refinement); } }} /><button type="button" onClick={() => onRetry(crop, refinement)} disabled={loading || !scope}>Apply</button></label>
    </div>}
    {error && <div className="visual-search-error" role="alert"><span>{error}</span><button type="button" onClick={() => onRetry(crop)} disabled={loading}>Retry</button></div>}
    {loading && <p className="visual-search-loading" role="status">Finding similar images…</p>}
    <input ref={inputRef} type="file" accept="image/*" hidden onChange={event => { const file = event.currentTarget.files?.[0]; if (file) onUpload(file); event.currentTarget.value = ""; }} />
  </section>;
}
