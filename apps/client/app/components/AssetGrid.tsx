import { useEffect, useRef, useState } from "react";
import type { Asset, AssetMetadata, AssetMetadataMap } from "../types";
import { AssetStatusBadge } from "./AssetStatusBadge";
import { fileTypeGlyph, fileTypeLabel, fileTypeTone, getFileType } from "../utils/fileType";


export const THUMBNAIL_CONCURRENCY_LIMIT = 8;
export const INITIAL_HIGH_PRIORITY_THUMBNAILS = 8;

export function thumbnailFetchPriority(index: number): "high" | "auto" {
  return index < INITIAL_HIGH_PRIORITY_THUMBNAILS ? "high" : "auto";
}

type ThumbnailQueueTicket = {
  cancel: () => void;
  release: () => void;
};

type ThumbnailQueueTask = {
  active: boolean;
  done: boolean;
  start: () => void;
};

export function createThumbnailLoadQueue(limit = THUMBNAIL_CONCURRENCY_LIMIT) {
  let active = 0;
  const pending: ThumbnailQueueTask[] = [];

  function drain() {
    while (active < limit && pending.length) {
      const task = pending.shift();
      if (!task || task.done) continue;
      task.active = true;
      active += 1;
      task.start();
    }
  }

  function finish(task: ThumbnailQueueTask) {
    if (task.done) return;
    task.done = true;
    if (task.active) active -= 1;
    else {
      const index = pending.indexOf(task);
      if (index >= 0) pending.splice(index, 1);
    }
    drain();
  }

  return {
    acquire(start: () => void): ThumbnailQueueTicket {
      const task: ThumbnailQueueTask = { active: false, done: false, start };
      pending.push(task);
      drain();
      return {
        cancel: () => finish(task),
        release: () => finish(task),
      };
    },
    activeCount: () => active,
    pendingCount: () => pending.filter(task => !task.done).length,
  };
}

const thumbnailLoadQueue = createThumbnailLoadQueue();

export function shouldLoadAssetThumbnail(inViewport: boolean, thumbnailUrl?: string | null): boolean {
  return inViewport && Boolean(thumbnailUrl);
}

function AssetPreview({ item, fetchPriority }: { item: Asset; fetchPriority: "high" | "auto" }) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const [thumbnailLoaded, setThumbnailLoaded] = useState(false);
  const [inViewport, setInViewport] = useState(false);
  const [grantedThumbnailUrl, setGrantedThumbnailUrl] = useState<string | null>(null);
  const previewRef = useRef<HTMLSpanElement>(null);
  const queueTicket = useRef<ThumbnailQueueTicket | null>(null);
  const canShowThumbnail = (item.kind === "image" || item.kind === "video")
    && Boolean(item.thumbnail_url)
    && !thumbnailFailed;

  useEffect(() => {
    setThumbnailFailed(false);
    setThumbnailLoaded(false);
    setGrantedThumbnailUrl(null);
  }, [item.id, item.thumbnail_url]);

  useEffect(() => {
    if (!canShowThumbnail) return;

    const target = previewRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setInViewport(true);
      return;
    }

    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      setInViewport(true);
      observer.disconnect();
    }, { rootMargin: "240px 0px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, [canShowThumbnail]);

  useEffect(() => {
    if (!shouldLoadAssetThumbnail(inViewport, item.thumbnail_url) || !item.thumbnail_url) return;
    const url = item.thumbnail_url;
    const ticket = thumbnailLoadQueue.acquire(() => setGrantedThumbnailUrl(url));
    queueTicket.current = ticket;
    return () => {
      ticket.cancel();
      if (queueTicket.current === ticket) queueTicket.current = null;
    };
  }, [inViewport, item.id, item.thumbnail_url]);

  function finishThumbnail() {
    queueTicket.current?.release();
    queueTicket.current = null;
  }

  if (!canShowThumbnail) {
    const type = getFileType(item.mime_type, item.kind);
    return <span className={"preview-fallback asset-file-icon " + fileTypeTone(type)} aria-label={fileTypeLabel(type)}>{fileTypeGlyph(type)}</span>;
  }

  const shouldLoad = grantedThumbnailUrl === item.thumbnail_url;
  return <span ref={previewRef} className="thumbnail-frame">
    {!thumbnailLoaded && <span className="thumbnail-skeleton" aria-hidden="true" />}
    {shouldLoad && <img
      className={"preview-thumbnail" + (thumbnailLoaded ? " is-loaded" : "")}
      src={item.thumbnail_url}
      alt=""
      loading="eager"
      decoding="async"
      fetchPriority={fetchPriority}
      referrerPolicy="no-referrer"
      onLoad={() => {
        finishThumbnail();
        setThumbnailLoaded(true);
      }}
      onError={() => {
        finishThumbnail();
        setThumbnailFailed(true);
      }}
    />}
    {item.kind === "video" && thumbnailLoaded && <span className="video-thumbnail-badge" aria-hidden="true">▶</span>}
  </span>;
}

function AssetMetadataBar({
  item,
  metadata,
  onRate,
}: {
  item: Asset;
  metadata?: AssetMetadata;
  onRate: (item: Asset, rating: number | null) => void;
}) {
  const visibility = metadata?.tag_ids.find(tag => tag === "public" || tag === "draft");

  return <div className="asset-metadata">
    <span className="asset-labels">
      {metadata && <AssetStatusBadge status={metadata.processing_status} />}
      {visibility && <span className={"asset-status " + visibility}>{visibility}</span>}
    </span>
    {item.kind !== "folder" && <span className="asset-rating" aria-label="Asset rating">
      {[1, 2, 3, 4, 5].map(star => <button
        key={star}
        type="button"
        className={(metadata?.rating || 0) >= star ? "filled" : ""}
        title={`Rate ${star} star${star > 1 ? "s" : ""}`}
        aria-label={`Rate ${item.name} ${star} star${star > 1 ? "s" : ""}`}
        onClick={() => onRate(item, metadata?.rating === star ? null : star)}
      >★</button>)}
    </span>}
  </div>;
}

export const SEARCH_RESULT_SKELETON_COUNT = 18;

export function AssetGridSkeleton({ count = SEARCH_RESULT_SKELETON_COUNT }: { count?: number }) {
  return <div className="grid grid-skeleton" role="status" aria-live="polite" aria-label="Loading search results">
    {Array.from({ length: count }, (_, index) => <article key={index} aria-hidden="true">
      <span className="asset-card-skeleton-preview" />
      <div className="asset-card-skeleton-details">
        <i />
        <i />
        <i />
      </div>
    </article>)}
  </div>;
}

type Props = {
  items: Asset[];
  path: Asset[];
  selected: Set<string>;
  metadataByItem: AssetMetadataMap;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (id: string) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
  onPreview: (item: Asset) => void;
  onRate: (item: Asset, rating: number | null) => void;
  onDetails: (item: Asset) => void;
  onFocus: (item: Asset) => void;
};

export function AssetGrid({
  items,
  path,
  selected,
  metadataByItem,
  onOpen,
  onToggle,
  onPrefetch,
  onCancelPrefetch,
  onPreview,
  onRate,
  onDetails,
  onFocus,
}: Props) {
  function resultAncestors(item: Asset) {
    if (
      !item.ancestor_ids?.length
      || item.ancestor_ids.length !== item.ancestor_names?.length
    ) return path;

    return item.ancestor_ids.map((id, index): Asset => ({
      provider: item.provider,
      id,
      name: item.ancestor_names?.[index] || "Folder",
      kind: "folder",
      mime_type: "application/vnd.google-apps.folder",
    }));
  }

  function openItem(item: Asset) {
    if (item.kind === "folder") onOpen(item.id, resultAncestors(item));
    else if (item.kind === "image" || item.kind === "video") onPreview(item);
  }

  return <div className="grid">
    {items.map((item, index) => <article
      className={selected.has(item.id) ? "selected" : ""}
      key={item.id}
      onClick={() => onFocus(item)}
      onPointerEnter={() => item.kind === "folder" && onPrefetch(item.id)}
      onPointerLeave={onCancelPrefetch}
    >
      <button className="asset-info" onClick={event => { event.stopPropagation(); onDetails(item); }} aria-label={"View details for " + item.name}>i</button>
      <button className="check" onClick={() => onToggle(item.id)}>{selected.has(item.id) ? "✓" : ""}</button>
      <button className={"preview " + item.kind} onDoubleClick={() => openItem(item)}>
        <AssetPreview item={item} fetchPriority={thumbnailFetchPriority(index)} />
      </button>
      <div>
        <button className="name" onDoubleClick={() => openItem(item)}>{item.name}</button>
        <small>{fileTypeLabel(getFileType(item.mime_type, item.kind))}{item.modified_at && item.kind !== "folder" ? " - " + new Date(item.modified_at).toLocaleDateString() : ""}</small>
        <AssetMetadataBar item={item} metadata={metadataByItem[item.id]} onRate={onRate} />
      </div>
    </article>)}
  </div>;
}
