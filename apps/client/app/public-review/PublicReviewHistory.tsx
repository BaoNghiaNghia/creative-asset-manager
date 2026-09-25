import { useEffect, useRef } from "react";
import type { PublicCommentActivity } from "./api";
import type { ReviewHistoryEntry } from "./viewHistory";
import { getFileType } from "../utils/fileType";

export type PublicActivityTab = "history" | "comments";

type Props = {
  entries: ReviewHistoryEntry[];
  comments: PublicCommentActivity[];
  commentsLoading: boolean;
  activeTab: PublicActivityTab | null;
  currentKey?: string;
  onSelectTab: (tab: PublicActivityTab) => void;
  onDismiss: () => void;
  onOpenHistory: (entry: ReviewHistoryEntry) => void;
  onOpenComment: (entry: PublicCommentActivity) => void;
  onClearHistory: () => void;
};

function groupLabel(value: number | string, now = Date.now()) {
  const viewed = new Date(value);
  const today = new Date(now);
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startViewed = new Date(viewed.getFullYear(), viewed.getMonth(), viewed.getDate()).getTime();
  const days = Math.floor((startToday - startViewed) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return "Earlier";
}

function relativeTime(value: number | string, now = Date.now()) {
  const timestamp = typeof value === "number" ? value : new Date(value).getTime();
  const minutes = Math.max(0, Math.floor((now - timestamp) / 60_000));
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

function CommentIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.5a7.5 7.5 0 0 1-7.8 7.5 8.4 8.4 0 0 1-3.4-.7L4 20l1.4-4A7.1 7.1 0 0 1 4 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z"/></svg>;
}

function avatarInitials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(value => value[0]).join("").toUpperCase() || "G";
}

function avatarTone(name: string) {
  let hash = 2166136261;
  for (const char of name.trim().toLowerCase()) {
    hash ^= char.codePointAt(0) || 0;
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash >>> 0) % 8;
}

function excerpt(value: string, limit = 108) {
  const text = value.replace(/\s+/g, " ").trim() || "Comment";
  return text.length > limit ? text.slice(0, limit - 1).trimEnd() + "…" : text;
}

export function PublicReviewHistory({
  entries,
  comments,
  commentsLoading,
  activeTab,
  currentKey,
  onSelectTab,
  onDismiss,
  onOpenHistory,
  onOpenComment,
  onClearHistory,
}: Props) {
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!activeTab) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && panelRef.current?.contains(target)) return;
      onDismiss();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
  }, [activeTab, onDismiss]);

  const historyGroups = ["Today", "Yesterday", "Earlier"].map(label => ({
    label,
    items: entries.filter(entry => groupLabel(entry.viewed_at) === label),
  })).filter(group => group.items.length);

  const commentGroups = ["Today", "Yesterday", "Earlier"].map(label => ({
    label,
    items: comments.filter(entry => groupLabel(entry.annotation.created_at) === label),
  })).filter(group => group.items.length);

  return <aside ref={panelRef} className={"public-history-panel " + (activeTab ? "open" : "collapsed")} aria-label="Review activity">
    {!activeTab ? <div className="public-activity-rail">
      <button type="button" className="public-history-rail" onClick={() => onSelectTab("history")} aria-expanded="false" title="History">
        <ClockIcon/><span>History</span>{entries.length > 0 && <b aria-label={entries.length + " history items"}>{entries.length}</b>}
      </button>
      <button type="button" className="public-history-rail public-comments-rail" onClick={() => onSelectTab("comments")} aria-expanded="false" title="Comments">
        <CommentIcon/><span>Comments</span>{comments.length > 0 && <b aria-label={comments.length + " comments"}>{comments.length}</b>}
      </button>
    </div> : <div className="public-history-drawer">
      <header>
        <button type="button" className="public-history-collapse" onClick={onDismiss} aria-label="Collapse activity" title="Collapse">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>
        </button>
        <div><strong>{activeTab === "history" ? "History" : "Comments"}</strong><small>{activeTab === "history" ? entries.length + " viewed · last 15 days" : comments.length + " comments in this share"}</small></div>
        {activeTab === "history" && entries.length > 0 && <button type="button" className="public-history-clear" onClick={onClearHistory}>Clear</button>}
      </header>
      <nav className="public-activity-tabs" aria-label="Activity tabs">
        <button type="button" className={activeTab === "history" ? "active" : ""} onClick={() => onSelectTab("history")}><ClockIcon/>History</button>
        <button type="button" className={activeTab === "comments" ? "active" : ""} onClick={() => onSelectTab("comments")}><CommentIcon/>Comments</button>
      </nav>
      <div className="public-history-list">
        {activeTab === "history" ? <>
          {!entries.length ? <div className="public-history-empty"><ClockIcon/><strong>No viewing history yet</strong><span>Images and videos you open on this device will appear here.</span></div> : historyGroups.map(group => <section key={group.label} className="public-history-group">
            <h3>{group.label}</h3>
            {group.items.map(entry => {
              const mediaType = getFileType(entry.media_type, undefined, entry.filename);
              const key = entry.asset_id + ":" + entry.source_asset_id;
              return <button type="button" key={key} className={"public-history-item" + (currentKey === key ? " active" : "")} onClick={() => onOpenHistory(entry)} aria-label={"Open " + entry.filename + " from history"}>
                <span className="public-history-thumb">
                  {entry.thumbnail_url ? <img src={entry.thumbnail_url} alt="" loading="lazy"/> : <span aria-hidden="true">{mediaType === "video" ? "▶" : "▧"}</span>}
                  {mediaType === "video" && <i aria-hidden="true">▶</i>}
                </span>
                <span className="public-history-copy"><b title={entry.filename}>{entry.filename}</b><small>{mediaType === "video" ? "Video" : mediaType === "image" ? "Image" : "File"} · {relativeTime(entry.viewed_at)}</small></span>
              </button>;
            })}
          </section>)}
        </> : <>
          {commentsLoading ? <div className="public-activity-loading" role="status"><span/><span/><span/></div> : !comments.length ? <div className="public-history-empty"><CommentIcon/><strong>No comments yet</strong><span>Comments and replies in this shared review will appear here.</span></div> : commentGroups.map(group => <section key={group.label} className="public-history-group public-comment-activity-group">
            <h3>{group.label}</h3>
            {group.items.map(entry => {
              const note = entry.annotation;
              const asset = entry.asset;
              const mediaType = getFileType(asset.media_type, undefined, asset.filename);
              const parent = note.parent_annotation_id ? comments.find(value => value.annotation.id === note.parent_annotation_id) : undefined;
              return <button type="button" key={note.id} className="public-comment-activity-item" onClick={() => onOpenComment(entry)} aria-label={"Open comment by " + note.author.display_name + " on " + asset.filename}>
                <span className="public-history-thumb">
                  {asset.thumbnail_url ? <img src={asset.thumbnail_url} alt="" loading="lazy"/> : <span aria-hidden="true">{mediaType === "video" ? "▶" : "▧"}</span>}
                  {mediaType === "video" && <i aria-hidden="true">▶</i>}
                </span>
                <span className="public-comment-activity-copy">
                  <span className="public-comment-activity-author"><i className={"public-comment-avatar public-avatar-tone-" + avatarTone(note.author.display_name)} aria-hidden="true">{avatarInitials(note.author.display_name)}</i><b>{note.author.display_name}</b><time dateTime={note.created_at}>{relativeTime(note.created_at)}</time></span>
                  {parent && <small className="public-comment-reply-label">↳ Reply to {parent.annotation.author.display_name}</small>}
                  <span className="public-comment-activity-text">{excerpt(note.plain_text)}</span>
                  <small className="public-comment-activity-file" title={asset.filename}>{asset.filename} · {mediaType === "video" ? "Video" : mediaType === "image" ? "Image" : "File"}</small>
                </span>
              </button>;
            })}
          </section>)}
        </>}
      </div>
    </div>}
  </aside>;
}
