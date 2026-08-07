import { useEffect, useState } from "react";
import type { Asset } from "../types";
import { assetPreviewUrl } from "../utils/mediaUrls";
import { isAvifAsset } from "../utils/fileType";

type Props = {
  item: Asset;
  onClose: () => void;
};

export const mediaPreviewUrl = assetPreviewUrl

export function previewUnavailableMessage(mimeType: string): string {
  return mimeType === "image/avif"
    ? "This AVIF image could not be previewed by this browser or the connected cloud provider."
    : "The connected cloud provider could not stream this file.";
}

export function MediaViewer({ item, onClose }: Props) {
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const mediaUrl = assetPreviewUrl(item);
  const isAvif = isAvifAsset(item);

  useEffect(() => {
    setFailed(false);
    setLoading(true);
  }, [item.id, mediaUrl]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [onClose]);

  return <div
    className="media-viewer"
    role="dialog"
    aria-modal="true"
    aria-label={"Preview " + item.name}
    onMouseDown={event => event.target === event.currentTarget && onClose()}
  >
    <div className={"media-viewer-panel " + item.kind + (loading && !failed ? " is-loading" : "")}>
      <div className="media-viewer-toolbar">
        <div>
          <strong title={item.name}>{item.name}</strong>
          <small>{item.mime_type}</small>
        </div>
        {item.web_url && <a href={item.web_url} target="_blank" rel="noreferrer">Open in source</a>}
        <button onClick={onClose} aria-label="Close preview" title="Close preview" autoFocus>×</button>
      </div>

      <div className="media-viewer-stage">
        {failed ? <div className="media-viewer-error">
          <strong>Preview unavailable</strong>
          <span>{previewUnavailableMessage(item.mime_type)}</span>
        </div> : item.kind === "video" && !isAvif ? <video
          src={mediaUrl}
          poster={item.thumbnail_url}
          controls
          autoPlay
          playsInline
          preload="metadata"
          onCanPlay={() => setLoading(false)}
          onError={() => { setLoading(false); setFailed(true); }}
        /> : <img
          src={mediaUrl}
          alt={item.name}
          draggable={false}
          onLoad={() => setLoading(false)}
          onError={() => { setLoading(false); setFailed(true); }}
        />}
        {loading && !failed && <div className="media-viewer-loading" role="status" aria-live="polite">
          <div className="media-viewer-loading-card">
            <span className="media-viewer-loading-spinner" aria-hidden="true" />
            <div>
              <strong>Preparing preview</strong>
              <span>Loading securely from the connected source…</span>
            </div>
          </div>
        </div>}
      </div>
    </div>
  </div>;
}
