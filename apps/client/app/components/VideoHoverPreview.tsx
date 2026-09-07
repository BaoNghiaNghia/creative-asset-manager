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

export function VideoHoverPreview({ item, visible, onClose, onMouseEnter, onMouseLeave }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
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
    setLoading(true);
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
          onCanPlay={() => setLoading(false)}
          onTimeUpdate={loopBestMatch}
          onError={() => { setLoading(false); setFailed(true); }}
          data-preview-start-seconds={startSeconds}
          data-preview-end-seconds={endSeconds}
        />}
      {loading && !failed && <div className="video-hover-preview-loading" role="status">Đang tải video…</div>}
    </div>
  </section>;
}
