import { useEffect, useRef, useState } from "react";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { buildVideoPlaybackUrl, invalidateExplorerPlaybackTicket, resolveExplorerPlaybackUrl, seekVideoAt } from "../utils/videoPlayback";
import { formatVideoTimestamp } from "./VideoSearchResults";
type Props = { item: VideoSearchItem; onClose: () => void };
export function VideoSearchPlayer({ item, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const fallbackMediaUrl = buildVideoPlaybackUrl(item);
  const targetSeconds = Math.max(0, item.best_match.start_ms / 1000);
  function seekToBestMatch() { if (videoRef.current) seekVideoAt(videoRef.current, item.best_match.start_ms); }

  useEffect(() => {
    let active = true;
    setFailed(false);
    setRetrying(false);
    setMediaUrl(null);
    if (!fallbackMediaUrl) return () => { active = false; };
    void resolveExplorerPlaybackUrl(item).then(url => {
      if (active) setMediaUrl(url);
    });
    const video = videoRef.current;
    if (video && video.readyState >= 1) seekToBestMatch();
    return () => { active = false; };
  }, [item.analysis_run_id, item.best_match.start_ms]);

  useEffect(() => { const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", closeOnEscape); return () => window.removeEventListener("keydown", closeOnEscape); }, [onClose]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    return () => {
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
  }, [item.analysis_run_id, mediaUrl]);

  function retryPlayback() {
    if (retrying) {
      setFailed(true);
      return;
    }
    setRetrying(true);
    invalidateExplorerPlaybackTicket(item);
    void resolveExplorerPlaybackUrl(item, true).then(url => {
      if (!url) {
        setFailed(true);
        return;
      }
      setMediaUrl(url);
      setFailed(false);
    }).catch(() => setFailed(true));
  }

  return <div className="media-viewer video-search-player" role="dialog" aria-modal="true" aria-label={"Play " + item.filename} onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <div className="media-viewer-panel video">
      <div className="media-viewer-toolbar"><div><strong title={item.filename}>{item.filename}</strong><small>Best match at {formatVideoTimestamp(item.best_match.start_ms)}</small></div><button type="button" onClick={onClose} aria-label="Close video player" title="Close video player" autoFocus>×</button></div>
      <div className="media-viewer-stage">
        {!fallbackMediaUrl ? <div className="media-viewer-error" role="alert"><strong>Playback unavailable</strong><span>Playback unavailable for this indexed video.</span></div>
          : !mediaUrl && !failed ? <div className="media-viewer-loading" role="status"><div className="media-viewer-loading-card"><span className="media-viewer-loading-spinner" aria-hidden="true" /><div><strong>Preparing playback</strong><span>Checking CDN cache…</span></div></div></div>
          : failed || !mediaUrl ? <div className="media-viewer-error" role="alert"><strong>Playback unavailable</strong><span>The original video could not be streamed. Check access and try again.</span></div>
          : <video ref={videoRef} key={item.analysis_run_id} src={mediaUrl} poster={item.thumbnail_url || undefined} controls playsInline preload="metadata" aria-label={"Original video " + item.filename} onLoadedMetadata={seekToBestMatch} onError={retryPlayback} data-target-seconds={targetSeconds} />}
      </div>
    </div>
  </div>;
}
