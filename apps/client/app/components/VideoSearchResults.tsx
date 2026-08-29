import { useEffect, useRef, useState } from "react";
import { buildVideoPlaybackUrl, playbackSeekSeconds } from "../utils/videoPlayback";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { VideoHoverPreview } from "./VideoHoverPreview";

export const VIDEO_HOVER_PREVIEW_DELAY_MS = 1000;
const VIDEO_HOVER_PREVIEW_CLOSE_DELAY_MS = 220;

export function formatVideoTimestamp(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return hours
    ? hours + ":" + String(minutes).padStart(2, "0") + ":" + remainder
    : String(minutes).padStart(2, "0") + ":" + remainder;
}

export function formatVideoDuration(milliseconds: number | null): string | null {
  return typeof milliseconds === "number" && milliseconds >= 0
    ? formatVideoTimestamp(milliseconds)
    : null;
}

export function videoThumbnailRequestUrl(url: string | null): string | null {
  if (!url) return null;
  const separator = url.indexOf("?");
  if (separator < 0) return url;
  const params = new URLSearchParams(url.slice(separator + 1));
  if (params.get("fallback") !== "video") return url;
  params.delete("fallback");
  const query = params.toString();
  return url.slice(0, separator) + (query ? "?" + query : "");
}

function VideoThumbnail({ item, fallbackFrame }: { item: VideoSearchItem; fallbackFrame?: string }) {
  const [failed, setFailed] = useState(false);
  const timestamp = formatVideoTimestamp(item.best_match.start_ms);
  const providerThumbnail = videoThumbnailRequestUrl(item.thumbnail_url);
  const thumbnail = (!failed && providerThumbnail) || fallbackFrame;

  useEffect(() => setFailed(false), [item.analysis_run_id, item.thumbnail_url]);

  if (!thumbnail) {
    return <div
      className="video-search-thumbnail video-search-thumbnail-fallback"
      role="img"
      aria-label={"Video placeholder for " + item.filename}
    >
      <span className="video-search-play" aria-hidden="true">Play</span>
      <span className="video-search-time" aria-hidden="true">{timestamp}</span>
    </div>;
  }

  return <div className="video-search-thumbnail">
    <img
      src={thumbnail}
      alt={"Thumbnail for " + item.filename}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
    <span className="video-search-play" aria-hidden="true">Play</span>
    <span className="video-search-time" aria-hidden="true">{timestamp}</span>
  </div>;
}

type SequenceFrameTask = { controller: AbortController; started: boolean; cancelled: boolean; run: (signal: AbortSignal) => Promise<void> };

export function createSequenceFrameQueue(limit = 2) {
  let active = 0;
  const pending: SequenceFrameTask[] = [];

  function drain() {
    while (active < limit && pending.length) {
      const task = pending.shift();
      if (!task || task.cancelled) continue;
      task.started = true;
      active += 1;
      void task.run(task.controller.signal).catch(() => undefined).finally(() => {
        active -= 1;
        drain();
      });
    }
  }

  return {
    enqueue(run: SequenceFrameTask["run"]) {
      const task: SequenceFrameTask = { controller: new AbortController(), started: false, cancelled: false, run };
      pending.push(task);
      drain();
      return () => {
        task.cancelled = true;
        task.controller.abort();
        if (!task.started) {
          const index = pending.indexOf(task);
          if (index >= 0) pending.splice(index, 1);
        }
      };
    },
    activeCount: () => active,
    pendingCount: () => pending.filter(task => !task.cancelled).length,
  };
}

const sequenceFrameQueue = createSequenceFrameQueue();

function waitForVideoEvent(video: HTMLVideoElement, name: "loadedmetadata" | "loadeddata" | "seeked", signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      video.removeEventListener(name, done);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
    };
    const done = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error("Video frame is unavailable")); };
    const aborted = () => { cleanup(); reject(new DOMException("Aborted", "AbortError")); };
    if (signal.aborted) return aborted();
    video.addEventListener(name, done, { once: true });
    video.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", aborted, { once: true });
  });
}

export async function captureVideoSequenceFrames(mediaUrl: string, startTimesMs: number[], signal: AbortSignal, elements?: { video: HTMLVideoElement; canvas: HTMLCanvasElement }): Promise<string[]> {
  const video = elements?.video ?? document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "metadata";
  try {
    const metadataReady = waitForVideoEvent(video, "loadedmetadata", signal);
    video.src = mediaUrl;
    video.load();
    await metadataReady;
    if (video.readyState < 2) await waitForVideoEvent(video, "loadeddata", signal);

    const width = Math.max(1, Math.min(240, video.videoWidth || 240));
    const height = Math.max(1, Math.round(width * ((video.videoHeight || 135) / (video.videoWidth || 240))));
    const canvas = elements?.canvas ?? document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Video frame canvas is unavailable");

    const frames: string[] = [];
    for (const startMs of startTimesMs) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const target = playbackSeekSeconds(startMs, video.duration);
      if (Math.abs(video.currentTime - target) > 0.01) {
        const seeked = waitForVideoEvent(video, "seeked", signal);
        video.currentTime = target;
        await seeked;
      }
      context.drawImage(video, 0, 0, width, height);
      frames.push(canvas.toDataURL("image/jpeg", 0.76));
    }
    return frames;
  } finally {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
}

function VideoSequenceStrip({ item, onOpen, onThumbnailFrame }: { item: VideoSearchItem; onOpen: (item: VideoSearchItem) => void; onThumbnailFrame: (frame: string | null) => void }) {
  const stripRef = useRef<HTMLDivElement>(null);
  const [inViewport, setInViewport] = useState(false);
  const [frames, setFrames] = useState<string[]>([]);
  const mediaUrl = buildVideoPlaybackUrl(item);
  const frameKey = [item.best_match.start_ms, ...item.matches.map(match => match.start_ms)].join(":");

  useEffect(() => {
    const target = stripRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setInViewport(true);
      return;
    }
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      setInViewport(true);
      observer.disconnect();
    }, { rootMargin: "160px 0px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setFrames([]);
    onThumbnailFrame(null);
    if (!inViewport || !mediaUrl) return;
    let mounted = true;
    const cancel = sequenceFrameQueue.enqueue(async signal => {
      const captured = await captureVideoSequenceFrames(mediaUrl, [item.best_match.start_ms, ...item.matches.map(match => match.start_ms)], signal);
      if (!mounted || signal.aborted) return;
      onThumbnailFrame(captured[0] || null);
      setFrames(captured.slice(1));
    });
    return () => { mounted = false; cancel(); };
  }, [frameKey, inViewport, item.analysis_run_id, mediaUrl]);

  if (!item.matches.length) return null;

  return <div ref={stripRef} className="video-sequence-strip" aria-label={"Matching sequences for " + item.filename} aria-busy={Boolean(mediaUrl) && frames.length !== item.matches.length}>
    {item.matches.map((match, index) => {
      const timestamp = formatVideoTimestamp(match.start_ms);
      return <button
        type="button"
        className="video-sequence-thumbnail"
        key={match.start_ms + ":" + match.end_ms + ":" + index}
        onClick={() => onOpen({ ...item, best_match: match })}
        aria-label={"Open sequence " + (index + 1) + " at " + timestamp}
        title={match.summary || "Sequence at " + timestamp}
      >
        {frames[index] ? <img src={frames[index]} alt="" /> : <span aria-hidden="true">Frame</span>}
        <b>{timestamp}</b>
      </button>;
    })}
  </div>;
}

export function VideoSearchResults({
  items,
  onOpen,
  onDetails,
}: {
  items: VideoSearchItem[];
  onOpen: (item: VideoSearchItem) => void;
  onDetails: (item: VideoSearchItem) => void;
}) {
  const [generatedThumbnails, setGeneratedThumbnails] = useState<Record<string, string>>({});
  const [hoverPreview, setHoverPreview] = useState<{ item: VideoSearchItem; anchor: HTMLElement; visible: boolean } | null>(null);
  const openTimer = useRef<number | null>(null);
  const closeTimer = useRef<number | null>(null);

  function clearTimer(ref: typeof openTimer) {
    if (ref.current === null) return;
    window.clearTimeout(ref.current);
    ref.current = null;
  }

  function cancelClose() {
    clearTimer(closeTimer);
  }

  function closeNow() {
    clearTimer(openTimer);
    clearTimer(closeTimer);
    setHoverPreview(null);
  }

  function scheduleClose() {
    clearTimer(openTimer);
    clearTimer(closeTimer);
    if (hoverPreview && !hoverPreview.visible) {
      setHoverPreview(null);
      return;
    }
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null;
      setHoverPreview(null);
    }, VIDEO_HOVER_PREVIEW_CLOSE_DELAY_MS);
  }

  function schedulePreview(item: VideoSearchItem, anchor: HTMLElement) {
    cancelClose();
    clearTimer(openTimer);
    if (hoverPreview && hoverPreview.item.analysis_run_id !== item.analysis_run_id) {
      setHoverPreview(null);
    }
    if (!buildVideoPlaybackUrl(item)) return;
    if (hoverPreview?.item.analysis_run_id === item.analysis_run_id) return;
    setHoverPreview({ item, anchor, visible: false });
    openTimer.current = window.setTimeout(() => {
      openTimer.current = null;
      if (!anchor.isConnected) return;
      setHoverPreview(current => current?.item.analysis_run_id === item.analysis_run_id
        ? { ...current, visible: true }
        : current);
    }, VIDEO_HOVER_PREVIEW_DELAY_MS);
  }

  useEffect(() => () => {
    clearTimer(openTimer);
    clearTimer(closeTimer);
  }, []);

  useEffect(() => {
    if (!hoverPreview) return;
    const stillVisible = items.some(item => item.analysis_run_id === hoverPreview.item.analysis_run_id);
    if (!stillVisible) closeNow();
  }, [items, hoverPreview]);

  return <><div className="video-search-grid" aria-label="Video search results">
    {items.map((item) => {
      const timestamp = formatVideoTimestamp(item.best_match.start_ms);
      const excerpt = item.best_match.visual_description || item.best_match.speech;
      const duration = formatVideoDuration(item.duration_ms);

      return <article
        className="video-search-card"
        key={item.analysis_run_id}
        aria-label={"Video result: " + item.filename}
        onMouseEnter={event => schedulePreview(item, event.currentTarget)}
        onMouseLeave={scheduleClose}
      >
        <button
          type="button"
          className="video-search-detail"
          onClick={() => onDetails(item)}
          aria-label={"View details for " + item.filename}
          title="View details"
        >i</button>
        <VideoThumbnail item={item} fallbackFrame={generatedThumbnails[item.analysis_run_id]} />
        <div className="video-search-card-body">
          <header className="video-search-card-header">
            <h3 title={item.filename}>{item.filename}</h3>
            <div className="video-search-meta">
              {duration && <span>Duration {duration}</span>}
              <span>{item.matches.length} matching segment{item.matches.length === 1 ? "" : "s"}</span>
            </div>
          </header>

          <VideoSequenceStrip item={item} onOpen={onOpen} onThumbnailFrame={frame => setGeneratedThumbnails(current => {
            if (frame && current[item.analysis_run_id] === frame) return current;
            if (!frame && !(item.analysis_run_id in current)) return current;
            const next = { ...current };
            if (frame) next[item.analysis_run_id] = frame;
            else delete next[item.analysis_run_id];
            return next;
          })} />

          <div className="video-search-best-match">
            <b>Best match - {timestamp}</b>
            <p>{item.best_match.summary}</p>
            {excerpt && <small>{excerpt}</small>}
          </div>

          <button
            type="button"
            className="video-search-open"
            onClick={() => onOpen(item)}
            aria-label={"Open " + item.filename + " at " + timestamp}
          >
            <span aria-hidden="true">Play</span>
            Open at {timestamp}
          </button>
        </div>
      </article>;
    })}
  </div>{hoverPreview && <VideoHoverPreview
    item={hoverPreview.item}
    anchor={hoverPreview.anchor}
    visible={hoverPreview.visible}
    onClose={closeNow}
    onMouseEnter={cancelClose}
    onMouseLeave={scheduleClose}
  />}</>;
}
