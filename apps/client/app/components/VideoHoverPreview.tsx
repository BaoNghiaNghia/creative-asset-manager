import { useEffect, useRef, useState } from "react";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { invalidateExplorerPlaybackTicket, resolveExplorerPlaybackUrl, seekVideoAt } from "../utils/videoPlayback";

type Props = {
  item: VideoSearchItem;
  visible: boolean;
  onClose: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
};

export function VideoHoverPreview({ item, visible, onClose, onMouseEnter, onMouseLeave }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [portrait, setPortrait] = useState(false);
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const startSeconds = Math.max(0, item.best_match.start_ms / 1000);
  const endSeconds = Math.max(startSeconds, item.best_match.end_ms / 1000);

  function startPlayback() {
    const video = videoRef.current;
    if (!video) return;
    seekVideoAt(video, item.best_match.start_ms);
    void video.play().catch(() => undefined);
  }

  function handleLoadedMetadata() {
    const video = videoRef.current;
    if (video) setPortrait(video.videoHeight > video.videoWidth);
    startPlayback();
  }

  function loopBestMatch() {
    const video = videoRef.current;
    if (!video || endSeconds <= startSeconds || video.currentTime < endSeconds) return;
    video.currentTime = startSeconds;
    void video.play().catch(() => undefined);
  }

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    const closeOnScroll = () => onClose();
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("scroll", closeOnScroll, true);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("scroll", closeOnScroll, true);
    };
  }, [onClose]);

  useEffect(() => {
    let active = true;
    setFailed(false);
    setLoading(true);
    setPortrait(false);
    setRetrying(false);
    setMediaUrl(null);
    void resolveExplorerPlaybackUrl(item).then(url => {
      if (active) setMediaUrl(url);
    });
    const video = videoRef.current;
    return () => {
      active = false;
      if (!video) return;
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
  }, [item.analysis_run_id, item.best_match.start_ms, item.external_asset_id, item.external_source_id, item.source_type]);

  function retryPlayback() {
    if (retrying) {
      setLoading(false);
      setFailed(true);
      return;
    }
    setRetrying(true);
    invalidateExplorerPlaybackTicket(item);
    void resolveExplorerPlaybackUrl(item, true).then(url => {
      if (!url) {
        setLoading(false);
        setFailed(true);
        return;
      }
      setMediaUrl(url);
      setFailed(false);
    }).catch(() => {
      setLoading(false);
      setFailed(true);
    });
  }

  return <section
    className={(visible ? "video-hover-preview" : "video-hover-preloader") + (portrait ? " video-hover-preview--portrait" : "")}
    role={visible ? "dialog" : undefined}
    aria-label={visible ? "Hover preview for " + item.filename : undefined}
    aria-hidden={visible ? undefined : true}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
  >
    <div className="video-hover-preview-stage">
      {failed
        ? <div className="video-hover-preview-error" role="alert">Video preview is unavailable.</div>
        : mediaUrl ? <video
          ref={videoRef}
          key={item.analysis_run_id + ":" + item.best_match.start_ms + ":" + mediaUrl}
          src={mediaUrl}
          poster={item.thumbnail_url || undefined}
          muted
          autoPlay
          controls={false}
          disablePictureInPicture
          controlsList="nodownload nofullscreen noremoteplayback"
          playsInline
          preload="auto"
          aria-label={"Preview " + item.filename}
          onLoadedMetadata={handleLoadedMetadata}
          onCanPlay={() => setLoading(false)}
          onTimeUpdate={loopBestMatch}
          onError={retryPlayback}
          data-preview-start-seconds={startSeconds}
          data-preview-end-seconds={endSeconds}
        /> : null}
      {loading && !failed && <div className="video-hover-preview-loading" role="status"><span>Đang tải video…</span></div>}
    </div>
  </section>;
}
