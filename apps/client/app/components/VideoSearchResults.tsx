import { useState } from "react";
import type { VideoSearchItem } from "../hooks/useVideoSearch";

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

function VideoThumbnail({ item }: { item: VideoSearchItem }) {
  const [failed, setFailed] = useState(false);
  const timestamp = formatVideoTimestamp(item.best_match.start_ms);

  if (!item.thumbnail_url || failed) {
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
      src={item.thumbnail_url}
      alt={"Thumbnail for " + item.filename}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
    <span className="video-search-play" aria-hidden="true">Play</span>
    <span className="video-search-time" aria-hidden="true">{timestamp}</span>
  </div>;
}

export function VideoSearchResults({
  items,
  onOpen,
}: {
  items: VideoSearchItem[];
  onOpen: (item: VideoSearchItem) => void;
}) {
  return <div className="video-search-grid" aria-label="Video search results">
    {items.map((item) => {
      const timestamp = formatVideoTimestamp(item.best_match.start_ms);
      const excerpt = item.best_match.visual_description || item.best_match.speech;
      const duration = formatVideoDuration(item.duration_ms);

      return <article
        className="video-search-card"
        key={item.analysis_run_id}
        aria-label={"Video result: " + item.filename}
      >
        <VideoThumbnail item={item} />
        <div className="video-search-card-body">
          <header className="video-search-card-header">
            <h3 title={item.filename}>{item.filename}</h3>
            <div className="video-search-meta">
              {duration && <span>Duration {duration}</span>}
              <span>{item.matches.length} matching segment{item.matches.length === 1 ? "" : "s"}</span>
            </div>
          </header>

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
  </div>;
}
