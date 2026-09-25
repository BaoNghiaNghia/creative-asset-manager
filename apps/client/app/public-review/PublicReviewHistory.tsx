import { useEffect, useRef } from "react";
import type { ReviewHistoryEntry } from "./viewHistory";
import { getFileType } from "../utils/fileType";

type Props = {
  entries: ReviewHistoryEntry[];
  open: boolean;
  currentKey?: string;
  onToggle: () => void;
  onOpen: (entry: ReviewHistoryEntry) => void;
  onClear: () => void;
};

function groupLabel(viewedAt: number, now = Date.now()) {
  const viewed = new Date(viewedAt);
  const today = new Date(now);
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startViewed = new Date(viewed.getFullYear(), viewed.getMonth(), viewed.getDate()).getTime();
  const days = Math.floor((startToday - startViewed) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return "Earlier";
}

function relativeTime(viewedAt: number, now = Date.now()) {
  const minutes = Math.max(0, Math.floor((now - viewedAt) / 60_000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return minutes + "m ago";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h ago";
  const days = Math.floor(hours / 24);
  return days + "d ago";
}

function ClockIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg>;
}

export function PublicReviewHistory({ entries, open, currentKey, onToggle, onOpen, onClear }: Props) {
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && panelRef.current?.contains(target)) return;
      onToggle();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
  }, [open, onToggle]);

  const groups = ["Today", "Yesterday", "Earlier"].map(label => ({
    label,
    items: entries.filter(entry => groupLabel(entry.viewed_at) === label),
  })).filter(group => group.items.length);

  return <aside ref={panelRef} className={"public-history-panel " + (open ? "open" : "collapsed")} aria-label="View history">
    {!open ? <button type="button" className="public-history-rail" onClick={onToggle} aria-expanded="false" title="History">
      <ClockIcon/>
      <span>History</span>
      {entries.length > 0 && <b aria-label={entries.length + " history items"}>{entries.length}</b>}
    </button> : <div className="public-history-drawer">
      <header>
        <button type="button" className="public-history-collapse" onClick={onToggle} aria-label="Collapse history" title="Collapse history">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
        </button>
        <div><strong>History</strong><small>{entries.length} viewed · last 15 days</small></div>
        {entries.length > 0 && <button type="button" className="public-history-clear" onClick={onClear}>Clear</button>}
      </header>
      <div className="public-history-list">
        {!entries.length ? <div className="public-history-empty"><ClockIcon/><strong>No viewing history yet</strong><span>Images and videos you open on this device will appear here.</span></div> : groups.map(group => <section key={group.label} className="public-history-group">
          <h3>{group.label}</h3>
          {group.items.map(entry => {
            const mediaType = getFileType(entry.media_type, undefined, entry.filename);
            const key = entry.asset_id + ":" + entry.source_asset_id;
            return <button type="button" key={key} className={"public-history-item" + (currentKey === key ? " active" : "")} onClick={() => onOpen(entry)} aria-label={"Open " + entry.filename + " from history"}>
              <span className="public-history-thumb">
                {entry.thumbnail_url ? <img src={entry.thumbnail_url} alt="" loading="lazy"/> : <span aria-hidden="true">{mediaType === "video" ? "▶" : "▧"}</span>}
                {mediaType === "video" && <i aria-hidden="true">▶</i>}
              </span>
              <span className="public-history-copy">
                <b title={entry.filename}>{entry.filename}</b>
                <small>{mediaType === "video" ? "Video" : mediaType === "image" ? "Image" : "File"} · {relativeTime(entry.viewed_at)}</small>
              </span>
            </button>;
          })}
        </section>)}
      </div>
    </div>}
  </aside>;
}
