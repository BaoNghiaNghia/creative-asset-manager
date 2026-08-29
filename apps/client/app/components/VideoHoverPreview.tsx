import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { buildVideoPlaybackUrl, seekVideoAt } from "../utils/videoPlayback";

type Position = { left: number; top: number; width: number };
type Props = {
  item: VideoSearchItem;
  anchor: HTMLElement;
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

export function hoverPreviewPosition(anchor: Pick<DOMRect, "left" | "top" | "width">, viewportWidth: number): Position {
  const gutter = 12;
  const width = Math.min(420, Math.max(280, viewportWidth - gutter * 2));
  const estimatedHeight = width * 9 / 16 + 46;
  const centered = anchor.left + anchor.width / 2 - width / 2;
  return {
    left: Math.max(gutter, Math.min(centered, viewportWidth - width - gutter)),
    top: Math.max(gutter, anchor.top - estimatedHeight - 10),
    width,
  };
}

export function VideoHoverPreview({ item, anchor, onClose, onMouseEnter, onMouseLeave }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [position, setPosition] = useState<Position>(() => hoverPreviewPosition(anchor.getBoundingClientRect(), window.innerWidth));
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

  useLayoutEffect(() => {
    const updatePosition = () => setPosition(hoverPreviewPosition(anchor.getBoundingClientRect(), window.innerWidth));
    updatePosition();
    window.addEventListener("resize", updatePosition);
    return () => window.removeEventListener("resize", updatePosition);
  }, [anchor]);

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

  return createPortal(<section
    className="video-hover-preview"
    style={position}
    role="dialog"
    aria-label={"Hover preview for " + item.filename}
    onMouseEnter={onMouseEnter}
    onMouseLeave={onMouseLeave}
  >
    <header>
      <div><strong title={item.filename}>{item.filename}</strong><small>Best match · {formatTimestamp(item.best_match.start_ms)}–{formatTimestamp(item.best_match.end_ms)}</small></div>
      <button type="button" onClick={onClose} aria-label="Close hover preview" title="Close preview">×</button>
    </header>
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
          controls
          playsInline
          preload="metadata"
          aria-label={"Preview " + item.filename}
          onLoadedMetadata={startPlayback}
          onTimeUpdate={loopBestMatch}
          onError={() => setFailed(true)}
          data-preview-start-seconds={startSeconds}
          data-preview-end-seconds={endSeconds}
        />}
    </div>
  </section>, document.body);
}
