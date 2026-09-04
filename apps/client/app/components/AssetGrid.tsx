import { useEffect, useRef, useState, type DragEvent, type MouseEvent, type PointerEvent } from "react";
import type { Asset, AssetMetadata, AssetMetadataMap } from "../types";
import { AssetStatusBadge } from "./AssetStatusBadge";
import { fileTypeGlyph, fileTypeLabel, fileTypeLogo, fileTypeTone, getFileType, isAvifAsset, isPreviewableAsset } from "../utils/fileType";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";


export const THUMBNAIL_CONCURRENCY_LIMIT = 6;
export const INITIAL_HIGH_PRIORITY_THUMBNAILS = 6;

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
  const avif = isAvifAsset(item);
  const mediaUrl = assetPreviewUrl(item);
  const thumbnailSourceUrl = avif ? mediaUrl : item.thumbnail_url;
  const previewUrl = thumbnailSourceUrl;
  const canShowThumbnail = (item.kind === "image" || item.kind === "video")
    && Boolean(thumbnailSourceUrl)
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
    if (!shouldLoadAssetThumbnail(inViewport, thumbnailSourceUrl) || !thumbnailSourceUrl) return;
    const url = thumbnailSourceUrl;
    const ticket = thumbnailLoadQueue.acquire(() => setGrantedThumbnailUrl(url));
    queueTicket.current = ticket;
    return () => {
      ticket.cancel();
      if (queueTicket.current === ticket) queueTicket.current = null;
    };
  }, [inViewport, item.id, thumbnailSourceUrl]);

  function finishThumbnail() {
    queueTicket.current?.release();
    queueTicket.current = null;
  }

  if (!canShowThumbnail) {
    const type = getFileType(item.mime_type, item.kind, item.name);
    return <span className={"preview-fallback asset-file-icon " + fileTypeTone(type)} aria-label={fileTypeLabel(type)}>{fileTypeLogo(type) ? <img className="google-workspace-file-logo" src={fileTypeLogo(type)!} alt="" /> : fileTypeGlyph(type)}</span>;
  }

  const shouldLoad = grantedThumbnailUrl === previewUrl;
  return <span ref={previewRef} className="thumbnail-frame">
    {!thumbnailLoaded && <span className="thumbnail-skeleton" aria-hidden="true" />}
    {shouldLoad && <img
      className={"preview-thumbnail" + (thumbnailLoaded ? " is-loaded" : "")}
      src={previewUrl || undefined}
      alt=""
      loading={fetchPriority === "high" ? "eager" : "lazy"}
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

export const ASSET_DRAG_OUT_MIME = "application/x-creative-asset-drag-out";

export type SelectionRectangle = {
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
};

type CardBounds = {
  id: string;
  rect: Pick<DOMRect, "left" | "right" | "top" | "bottom">;
};

export function assetIdsInSelectionRectangle(
  selection: SelectionRectangle,
  cards: Iterable<CardBounds>,
): string[] {
  const left = Math.min(selection.startX, selection.currentX);
  const right = Math.max(selection.startX, selection.currentX);
  const top = Math.min(selection.startY, selection.currentY);
  const bottom = Math.max(selection.startY, selection.currentY);
  return [...cards].filter(({ rect }) =>
    rect.left < right && rect.right > left && rect.top < bottom && rect.bottom > top,
  ).map(({ id }) => id);
}

function safeDownloadName(name: string): string {
  return name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-") || "asset";
}

export function originalAssetDragPayload(items: Asset[], origin: string) {
  const urls = items.map(item => new URL(explorerAssetUrl(item, "media"), origin).toString());
  const first = items[0];
  return {
    downloadUrl: first ? `${first.mime_type || "application/octet-stream"}:${safeDownloadName(first.name)}:${urls[0]}` : "",
    uriList: urls.join("\r\n"),
    text: urls.join("\n"),
    sourceIds: JSON.stringify(items.map(item => item.id)),
  };
}

function dragTypesIncludeAssetPayload(types: Iterable<string>): boolean {
  return [...types].includes(ASSET_DRAG_OUT_MIME);
}

export function isAdditiveSelectionClick(event: Pick<MouseEvent, "ctrlKey" | "metaKey">): boolean {
  return event.ctrlKey || event.metaKey;
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
  onReplaceSelection: (ids: Iterable<string>) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
  onPreview: (item: Asset) => void;
  onRate: (item: Asset, rating: number | null) => void;
  onDetails: (item: Asset) => void;
  onFocus: (item: Asset) => void;
  onContextMenu: (item: Asset, event: MouseEvent<HTMLElement>) => void;
};

export function AssetGrid({
  items,
  path,
  selected,
  metadataByItem,
  onOpen,
  onToggle,
  onReplaceSelection,
  onPrefetch,
  onCancelPrefetch,
  onPreview,
  onRate,
  onDetails,
  onFocus,
  onContextMenu,
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
    if (item.kind === "folder") {
      onOpen(item.id, resultAncestors(item));
      return;
    }
    if (isPreviewableAsset(item)) onPreview(item);
  }

  const gridRef = useRef<HTMLDivElement>(null);
  const marqueeRef = useRef<{ pointerId: number; baseline: Set<string>; selection: SelectionRectangle; moved: boolean } | null>(null);
  const [marquee, setMarquee] = useState<SelectionRectangle | null>(null);

  useEffect(() => {
    const rejectInternalDrop = (event: globalThis.DragEvent) => {
      if (!dragTypesIncludeAssetPayload(event.dataTransfer?.types || [])) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
    };
    document.addEventListener("dragover", rejectInternalDrop, true);
    document.addEventListener("drop", rejectInternalDrop, true);
    return () => {
      document.removeEventListener("dragover", rejectInternalDrop, true);
      document.removeEventListener("drop", rejectInternalDrop, true);
    };
  }, []);

  function updateMarquee(event: PointerEvent<HTMLDivElement>) {
    const active = marqueeRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const selection = { ...active.selection, currentX: event.clientX, currentY: event.clientY };
    if (!active.moved && Math.hypot(selection.currentX - selection.startX, selection.currentY - selection.startY) < 4) return;
    active.moved = true;
    active.selection = selection;
    const cards = [...(gridRef.current?.querySelectorAll<HTMLElement>("[data-asset-id]") || [])]
      .map(card => ({ id: card.dataset.assetId || "", rect: card.getBoundingClientRect() }))
      .filter(card => Boolean(card.id));
    onReplaceSelection(new Set([...active.baseline, ...assetIdsInSelectionRectangle(selection, cards)]));
    setMarquee(selection);
  }

  function finishMarquee(event: PointerEvent<HTMLDivElement>) {
    const active = marqueeRef.current;
    if (!active || active.pointerId !== event.pointerId) return;
    marqueeRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!active.moved && event.target === event.currentTarget && !event.ctrlKey && !event.metaKey) onReplaceSelection([]);
    setMarquee(null);
  }

  function startMarquee(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !(event.target instanceof Element) || event.target.closest("button, a, input, textarea, select, [draggable=\"true\"]")) return;
    const selection = { startX: event.clientX, startY: event.clientY, currentX: event.clientX, currentY: event.clientY };
    marqueeRef.current = {
      pointerId: event.pointerId,
      baseline: event.ctrlKey || event.metaKey ? new Set(selected) : new Set(),
      selection,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function dragOriginalFiles(event: DragEvent<HTMLElement>, item: Asset) {
    marqueeRef.current = null;
    setMarquee(null);
    if (item.kind === "folder") {
      event.preventDefault();
      return;
    }
    const dragItems = selected.has(item.id)
      ? items.filter(candidate => selected.has(candidate.id) && candidate.kind !== "folder")
      : [item];
    const payload = originalAssetDragPayload(dragItems, window.location.origin);
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData(ASSET_DRAG_OUT_MIME, payload.sourceIds);
    event.dataTransfer.setData("DownloadURL", payload.downloadUrl);
    event.dataTransfer.setData("text/uri-list", payload.uriList);
    event.dataTransfer.setData("text/plain", payload.text);
  }

  function blockInternalDrop(event: DragEvent<HTMLDivElement>) {
    if (!dragTypesIncludeAssetPayload(event.dataTransfer.types)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "none";
  }

  const marqueeStyle = marquee ? {
    left: Math.min(marquee.startX, marquee.currentX),
    top: Math.min(marquee.startY, marquee.currentY),
    width: Math.abs(marquee.currentX - marquee.startX),
    height: Math.abs(marquee.currentY - marquee.startY),
  } : undefined;

  return <div
    ref={gridRef}
    className="grid"
    onPointerDown={startMarquee}
    onPointerMove={updateMarquee}
    onPointerUp={finishMarquee}
    onPointerCancel={finishMarquee}
    onDragOverCapture={blockInternalDrop}
    onDropCapture={blockInternalDrop}
  >
    {marquee && <span className="asset-selection-marquee" style={marqueeStyle} aria-hidden="true" />}
    {items.map((item, index) => <article
      className={(selected.has(item.id) ? "selected" : "") + ((item.kind === "image" || item.kind === "video") ? " media-card" : "")}
      key={item.id}
      data-asset-id={item.id}
      draggable={item.kind !== "folder"}
      title={item.kind === "folder" ? undefined : "Drag the original file to another application"}
      onDragStart={event => dragOriginalFiles(event, item)}
      onClick={event => {
        if (isAdditiveSelectionClick(event)) {
          onToggle(item.id);
          return;
        }
        onFocus(item);
      }}
      onContextMenu={event => onContextMenu(item, event)}
      onPointerEnter={() => item.kind === "folder" && onPrefetch(item.id)}
      onPointerLeave={onCancelPrefetch}
    >
      <button className="asset-info" onClick={event => { event.stopPropagation(); onDetails(item); }} aria-label={"View details for " + item.name}>i</button>
      <button className="check" onClick={event => { event.stopPropagation(); onToggle(item.id); }}>{selected.has(item.id) ? "✓" : ""}</button>
      <button className={"preview " + item.kind} onDoubleClick={() => openItem(item)}>
        <AssetPreview item={item} fetchPriority={thumbnailFetchPriority(index)} />
        {item.kind === "video" && <span className="video-thumbnail-badge" aria-hidden="true">▶</span>}
      </button>
      <div>
        <button className="name" onDoubleClick={() => openItem(item)}>{item.name}</button>
        <small>{fileTypeLabel(getFileType(item.mime_type, item.kind, item.name))}{item.modified_at && item.kind !== "folder" ? " - " + new Date(item.modified_at).toLocaleDateString() : ""}</small>
        <AssetMetadataBar item={item} metadata={metadataByItem[item.id]} onRate={onRate} />
      </div>
    </article>)}
  </div>;
}
