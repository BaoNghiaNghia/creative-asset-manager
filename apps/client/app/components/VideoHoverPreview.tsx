import { useEffect, useRef, useState } from "react";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { buildVideoPlaybackUrl, seekVideoAt } from "../utils/videoPlayback";

type Props = {
  item: VideoSearchItem;
  visible: boolean;
  onClose: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
};

function formatTimestamp(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return hours
    ? hours + ":" + String(minutes).padStart(2, "0") + ":" + remainder
    : String(minutes).padStart(2, "0") + ":" + remainder;
}

export function VideoHoverPreview({ item, visible, onClose, onMouseEnter, onMouseLeave }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const mediaUrl = buildVideoPlaybackUrl(item);
  const startSeconds = Math.max(0, item.best_match.start_ms / 1000);
  const endSeconds = Math.max(startSeconds, item.best_match.end_ms / 1000);

  function startPlayback() {
    const video = videoRef.current;
    if (!video) return;
    seekVideoAt(video, item.best_match.start_ms);
    void video.play().catch(() => undefined);
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
    setFailed(false);
    const video = videoRef.current;
    return () => {
      if (!video) return;
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
  }, [item.analysis_run_id, item.best_match.start_ms]);

  return <section
    className={visible ? "video-hover-preview" : "video-hover-preloader"}
    role={visible ? "dialog" : undefined}
    aria-label={visible ? "Hover preview for " + item.filename : undefined}
    aria-hidden={visible ? undefined : true}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
  >
    {visible && <header>
      <div><strong title={item.filename}>{item.filename}</strong><small>Best match · {formatTimestamp(item.best_match.start_ms)}–{formatTimestamp(item.best_match.end_ms)}</small></div>
      <button type="button" onClick={onClose} aria-label="Close hover preview" title="Close preview">×</button>
    </header>}
    <div className="video-hover-preview-stage">
      {!mediaUrl || failed
        ? <div className="video-hover-preview-error" role="alert">Video preview is unavailable.</div>
        : <video
          ref={videoRef}
          key={item.analysis_run_id + ":" + item.best_match.start_ms}
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
          onLoadedMetadata={startPlayback}
          onTimeUpdate={loopBestMatch}
          onError={() => setFailed(true)}
          data-preview-start-seconds={startSeconds}
          data-preview-end-seconds={endSeconds}
        />}
    </div>
  </section>;
}
