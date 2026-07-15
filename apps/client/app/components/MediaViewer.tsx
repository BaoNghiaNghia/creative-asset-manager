import { useEffect, useState } from "react";
import type { Asset } from "../types";

type Props = {
  item: Asset;
  onClose: () => void;
};

export function MediaViewer({ item, onClose }: Props) {
  const [failed, setFailed] = useState(false);
  const mediaUrl = "/api/explorer/media/" + encodeURIComponent(item.id);

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
    <div className={"media-viewer-panel " + item.kind}>
      <div className="media-viewer-toolbar">
        <div>
          <strong title={item.name}>{item.name}</strong>
          <small>{item.mime_type}</small>
        </div>
        {item.web_url && <a href={item.web_url} target="_blank" rel="noreferrer">Open in Drive</a>}
        <button onClick={onClose} aria-label="Close preview" title="Close preview" autoFocus>×</button>
      </div>

      <div className="media-viewer-stage">
        {failed ? <div className="media-viewer-error">
          <strong>Preview unavailable</strong>
          <span>Google Drive could not stream this file.</span>
        </div> : item.kind === "video" ? <video
          src={mediaUrl}
          poster={item.thumbnail_url}
          controls
          autoPlay
          playsInline
          preload="metadata"
          onError={() => setFailed(true)}
        /> : <img
          src={mediaUrl}
          alt={item.name}
          draggable={false}
          onError={() => setFailed(true)}
        />}
      </div>
    </div>
  </div>;
}
