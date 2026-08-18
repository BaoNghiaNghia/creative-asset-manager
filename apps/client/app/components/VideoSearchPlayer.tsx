import { useEffect, useRef, useState } from "react";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { buildVideoPlaybackUrl, seekVideoAt } from "../utils/videoPlayback";
import { formatVideoTimestamp } from "./VideoSearchResults";
type Props = { item: VideoSearchItem; onClose: () => void };
export function VideoSearchPlayer({ item, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const mediaUrl = buildVideoPlaybackUrl(item);
  const targetSeconds = Math.max(0, item.best_match.start_ms / 1000);
  function seekToBestMatch() { if (videoRef.current) seekVideoAt(videoRef.current, item.best_match.start_ms); }
  useEffect(() => { setFailed(false); const video = videoRef.current; if (video && video.readyState >= 1) seekToBestMatch(); }, [item.analysis_run_id, item.best_match.start_ms]);
  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, [onClose]);
  useEffect(() => () => { const video = videoRef.current; if (!video) return; video.pause(); video.removeAttribute("src"); video.load(); }, [item.analysis_run_id]);
  return <div className="media-viewer video-search-player" role="dialog" aria-modal="true" aria-label={"Play " + item.filename} onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <div className="media-viewer-panel video">
      <div className="media-viewer-toolbar"><div><strong title={item.filename}>{item.filename}</strong><small>Best match at {formatVideoTimestamp(item.best_match.start_ms)}</small></div><button type="button" onClick={onClose} aria-label="Close video player" title="Close video player" autoFocus>Close</button></div>
      <div className="media-viewer-stage">
        {!mediaUrl ? <div className="media-viewer-error" role="alert"><strong>Playback unavailable</strong><span>Playback unavailable for this indexed video.</span></div>
          : failed ? <div className="media-viewer-error" role="alert"><strong>Playback unavailable</strong><span>The original video could not be streamed. Check access and try again.</span></div>
          : <video ref={videoRef} key={item.analysis_run_id} src={mediaUrl} poster={item.thumbnail_url || undefined} controls playsInline preload="metadata" aria-label={"Original video " + item.filename} onLoadedMetadata={seekToBestMatch} onError={() => setFailed(true)} data-target-seconds={targetSeconds} />}
      </div>
    </div>
  </div>;
}
