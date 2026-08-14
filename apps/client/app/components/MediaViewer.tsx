import { useEffect, useState } from "react";
import type { Asset } from "../types";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";
import { isAvifAsset, isTextAsset } from "../utils/fileType";
import { readTextPreview, TEXT_PREVIEW_MAX_BYTES, TEXT_PREVIEW_RANGE } from "../utils/textPreview";

type Props = { item: Asset; onClose: () => void };

export { readTextPreview, TEXT_PREVIEW_MAX_BYTES, TEXT_PREVIEW_RANGE } from "../utils/textPreview";
export const mediaPreviewUrl = assetPreviewUrl;

export function previewUnavailableMessage(mimeType: string): string {
  if (mimeType.split(";", 1)[0].trim().toLowerCase() === "text/plain") return "This text file could not be loaded from the connected cloud provider.";
  return mimeType === "image/avif" ? "This AVIF image could not be previewed by this browser or the connected cloud provider." : "The connected cloud provider could not stream this file.";
}

export function MediaViewer({ item, onClose }: Props) {
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const mediaUrl = assetPreviewUrl(item);
  const isAvif = isAvifAsset(item);
  const isText = isTextAsset(item);
  const textUrl = explorerAssetUrl(item, "media");

  useEffect(() => { setFailed(false); setLoading(true); setText(""); setTruncated(false); setCopied(false); setCopyFailed(false); }, [item.id, mediaUrl]);

  useEffect(() => {
    if (!isText) return;
    const controller = new AbortController();
    void fetch(textUrl, { signal: controller.signal, credentials: "same-origin", headers: { Range: TEXT_PREVIEW_RANGE } })
      .then(response => readTextPreview(response, controller.signal))
      .then(result => { if (!controller.signal.aborted) { setText(result.text); setTruncated(result.truncated); setLoading(false); } })
      .catch(error => { if (!controller.signal.aborted && error?.name !== "AbortError") { setFailed(true); setLoading(false); } });
    return () => controller.abort();
  }, [isText, item.id, textUrl]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previousOverflow; window.removeEventListener("keydown", closeOnEscape); };
  }, [onClose]);

  async function copyAll() {
    try { await navigator.clipboard.writeText(text); setCopyFailed(false); setCopied(true); window.setTimeout(() => setCopied(false), 1800); } catch { setCopied(false); setCopyFailed(true); }
  }

  return <div className={"media-viewer" + (isText ? " media-viewer-text-fullscreen" : "")} role="dialog" aria-modal="true" aria-label={"Preview " + item.name} onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <div className={"media-viewer-panel " + (isText ? "text" : item.kind) + (loading && !failed ? " is-loading" : "")}>
      <div className="media-viewer-toolbar"><div><strong title={item.name}>{item.name}</strong><small>{item.mime_type}</small></div>{item.web_url && <a href={item.web_url} target="_blank" rel="noreferrer">Open in source</a>}<button onClick={onClose} aria-label="Close preview" title="Close preview" autoFocus>×</button></div>
      <div className="media-viewer-stage">
        {failed ? <div className="media-viewer-error"><strong>Preview unavailable</strong><span>{previewUnavailableMessage(isText ? "text/plain" : item.mime_type)}</span></div>
          : isText ? <div className="media-viewer-text">{!loading && (text ? <pre className="media-viewer-text-content">{text}</pre> : <p>This text file is empty.</p>)}</div>
          : item.kind === "video" && !isAvif ? <video src={mediaUrl} poster={item.thumbnail_url} controls autoPlay playsInline preload="metadata" onCanPlay={() => setLoading(false)} onError={() => { setLoading(false); setFailed(true); }} />
          : <img src={mediaUrl} alt={item.name} draggable={false} onLoad={() => setLoading(false)} onError={() => { setLoading(false); setFailed(true); }} />}
        {loading && !failed && <div className="media-viewer-loading" role="status" aria-live="polite"><div className="media-viewer-loading-card"><span className="media-viewer-loading-spinner" aria-hidden="true" /><div><strong>Preparing preview</strong><span>Loading securely from the connected source…</span></div></div></div>}
      </div>
      {isText && !failed && !loading && <div className="media-viewer-text-footer"><span>{copyFailed ? "Copy failed. Select the text to copy it manually." : truncated ? "Preview limited to the first 1 MB." : "Text preview"}</span><button type="button" onClick={() => void copyAll()}>{copied ? "Copied" : "Copy all"}</button></div>}
    </div>
  </div>;
}
