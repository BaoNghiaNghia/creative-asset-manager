import { useEffect, useRef, useState, type DragEvent, type MouseEvent, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import type { Asset, AssetMetadata, AssetMetadataMap } from "../types";
import { AssetStatusBadge } from "./AssetStatusBadge";
import { VisualSearchIcon } from "./VisualSearchIcon";
import { fileTypeGlyph, fileTypeLabel, fileTypeLogo, fileTypeTone, getFileType, isAvifAsset, isPreviewableAsset } from "../utils/fileType";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";


export const THUMBNAIL_CONCURRENCY_LIMIT = 6;
export const INITIAL_HIGH_PRIORITY_THUMBNAILS = 6;
export const VIDEO_THUMBNAIL_RETRY_DELAYS_MS = [10_000, 15_000, 25_000, 40_000] as const;
export const VIDEO_THUMBNAIL_RECENT_WINDOW_MS = 10 * 60 * 1000;

export function shouldRetryVideoThumbnail(
  item: Pick<Asset, "kind" | "modified_at">,
  retryAttempt: number,
  now = Date.now(),
): boolean {
  if (item.kind !== "video" || retryAttempt >= VIDEO_THUMBNAIL_RETRY_DELAYS_MS.length || !item.modified_at) return false;
  const modifiedAt = Date.parse(item.modified_at);
  if (!Number.isFinite(modifiedAt)) return false;
  const age = now - modifiedAt;
  return age >= -60_000 && age <= VIDEO_THUMBNAIL_RECENT_WINDOW_MS;
}

export function thumbnailRetryUrl(url: string, retryAttempt: number): string {
  if (retryAttempt <= 0) return url;
  return url + (url.includes("?") ? "&" : "?") + "thumbnail_retry=" + retryAttempt;
}

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
  const [thumbnailRetryAttempt, setThumbnailRetryAttempt] = useState(0);
  const [inViewport, setInViewport] = useState(false);
  const [grantedThumbnailUrl, setGrantedThumbnailUrl] = useState<string | null>(null);
  const previewRef = useRef<HTMLSpanElement>(null);
  const queueTicket = useRef<ThumbnailQueueTicket | null>(null);
  const thumbnailRetryTimer = useRef<number | undefined>(undefined);
  const avif = isAvifAsset(item);
  const mediaUrl = assetPreviewUrl(item);
  const thumbnailSourceUrl = avif ? mediaUrl : item.thumbnail_url;
  const previewUrl = thumbnailSourceUrl ? thumbnailRetryUrl(thumbnailSourceUrl, thumbnailRetryAttempt) : thumbnailSourceUrl;
  const canShowThumbnail = (item.kind === "image" || item.kind === "video")
    && Boolean(thumbnailSourceUrl)
    && !thumbnailFailed;

  useEffect(() => {
    window.clearTimeout(thumbnailRetryTimer.current);
    thumbnailRetryTimer.current = undefined;
    setThumbnailFailed(false);
    setThumbnailLoaded(false);
    setThumbnailRetryAttempt(0);
    setGrantedThumbnailUrl(null);
    return () => {
      window.clearTimeout(thumbnailRetryTimer.current);
      thumbnailRetryTimer.current = undefined;
    };
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
    if (!shouldLoadAssetThumbnail(inViewport, previewUrl) || !previewUrl || thumbnailFailed) return;
    const url = previewUrl;
    const ticket = thumbnailLoadQueue.acquire(() => setGrantedThumbnailUrl(url));
    queueTicket.current = ticket;
    return () => {
      ticket.cancel();
      if (queueTicket.current === ticket) queueTicket.current = null;
    };
  }, [inViewport, item.id, previewUrl, thumbnailFailed]);

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
        setThumbnailLoaded(false);
        if (shouldRetryVideoThumbnail(item, thumbnailRetryAttempt)) {
          window.clearTimeout(thumbnailRetryTimer.current);
          setThumbnailFailed(true);
          const delay = VIDEO_THUMBNAIL_RETRY_DELAYS_MS[thumbnailRetryAttempt];
          thumbnailRetryTimer.current = window.setTimeout(() => {
            thumbnailRetryTimer.current = undefined;
            setGrantedThumbnailUrl(null);
            setThumbnailRetryAttempt(current => current + 1);
            setThumbnailFailed(false);
          }, delay);
          return;
        }
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
  const showProcessingStatus = item.kind !== "folder" && Boolean(metadata);

  if (!showProcessingStatus && !visibility && item.kind === "folder") return null;

  return <div className="asset-metadata">
    <span className="asset-labels">
      {showProcessingStatus && metadata && <AssetStatusBadge status={metadata.processing_status} />}
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

export function nativeOriginalDragItems(items: Asset[]): DesktopNativeDragAsset[] {
  return items.map(item => ({
    id: item.id,
    name: item.name,
    mimeType: item.mime_type || "application/octet-stream",
    provider: item.provider,
    externalSourceId: item.external_source_id,
    modifiedAt: item.modified_at,
    size: item.size,
  }));
}

export function nativeOriginalDragKey(items: DesktopNativeDragAsset[]): string {
  return JSON.stringify(items.map(item => [
    item.provider,
    item.externalSourceId || "",
    item.id,
    item.modifiedAt || "",
    item.size ?? "",
  ]));
}

export function nativeOriginalDragMode(
  hasDesktop: boolean,
  hasPreparedTicket: boolean,
  canStartItems: boolean,
): "prepared" | "direct" | "deferred" | "web" {
  if (!hasDesktop) return "web";
  if (hasPreparedTicket) return "prepared";
  return canStartItems ? "direct" : "deferred";
}

const MAX_NATIVE_PREWARM_FILES = 3;
const MAX_NATIVE_PREWARM_BYTES = 256 * 1024 * 1024;

export function nativeOriginalPrewarmItems(items: Asset[], preferredId: string): Asset[] {
  const ordered = [
    ...items.filter(item => item.id === preferredId),
    ...items.filter(item => item.id !== preferredId),
  ];
  const selected: Asset[] = [];
  let totalBytes = 0;
  for (const item of ordered) {
    if (selected.length >= MAX_NATIVE_PREWARM_FILES) break;
    if (item.kind === "folder" || !item.size || item.size <= 0) continue;
    if (item.size > MAX_NATIVE_PREWARM_BYTES) continue;
    if (totalBytes + item.size > MAX_NATIVE_PREWARM_BYTES) continue;
    selected.push(item);
    totalBytes += item.size;
  }
  return selected;
}

function dragTypesIncludeAssetPayload(types: Iterable<string>): boolean {
  return [...types].includes(ASSET_DRAG_OUT_MIME);
}

export function isAdditiveSelectionClick(event: Pick<MouseEvent, "ctrlKey" | "metaKey">): boolean {
  return event.ctrlKey || event.metaKey;
}

export function explorerSelectionForClick(
  orderedIds: readonly string[],
  current: ReadonlySet<string>,
  clickedId: string,
  anchorId: string | null,
  event: Pick<MouseEvent, "ctrlKey" | "metaKey" | "shiftKey">,
): { selected: Set<string>; anchorId: string } {
  const additive = event.ctrlKey || event.metaKey;
  const clickedIndex = orderedIds.indexOf(clickedId);
  const anchorIndex = anchorId ? orderedIds.indexOf(anchorId) : -1;

  if (event.shiftKey && clickedIndex >= 0 && anchorIndex >= 0) {
    const start = Math.min(clickedIndex, anchorIndex);
    const end = Math.max(clickedIndex, anchorIndex);
    const next = additive ? new Set(current) : new Set<string>();
    orderedIds.slice(start, end + 1).forEach(id => next.add(id));
    return { selected: next, anchorId: anchorId! };
  }

  if (additive) {
    const next = new Set(current);
    next.has(clickedId) ? next.delete(clickedId) : next.add(clickedId);
    return { selected: next, anchorId: clickedId };
  }

  return { selected: new Set([clickedId]), anchorId: clickedId };
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
  onPrefetchNow?: (id: string) => void;
  onCancelPrefetch: () => void;
  onPreview: (item: Asset) => void;
  onRate: (item: Asset, rating: number | null) => void;
  onDetails: (item: Asset) => void;
  onFocus: (item: Asset) => void;
  onContextMenu: (item: Asset, event: MouseEvent<HTMLElement>) => void;
  onFindSimilar?: (item: Asset) => void;
  reviewLinkShareIds?: ReadonlyMap<string, string>;
  activeExternalSourceId?: string | null;
  onCopyReviewLink?: (shareId: string, item: Asset) => void | Promise<void>;
  onRefreshReviewLink?: (shareId: string, item: Asset) => void | Promise<void>;
};

type FolderShareMenuState = {
  shareId: string;
  item: Asset;
  left: number;
  top: number;
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
  onPrefetchNow,
  onCancelPrefetch,
  onPreview,
  onRate,
  onDetails,
  onFocus,
  onContextMenu,
  onFindSimilar,
  reviewLinkShareIds,
  activeExternalSourceId,
  onCopyReviewLink,
  onRefreshReviewLink,
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
  const selectionAnchorRef = useRef<string | null>(null);
  const marqueeRef = useRef<{ pointerId: number; baseline: Set<string>; selection: SelectionRectangle; moved: boolean } | null>(null);
  const nativeDragPrewarmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nativeDragTickets = useRef(new Map<string, { ticket: string; expiresAt: number }>());
  const nativeDragPreparing = useRef(new Map<string, Promise<void>>());
  const [marquee, setMarquee] = useState<SelectionRectangle | null>(null);
  const [shareMenu, setShareMenu] = useState<FolderShareMenuState | null>(null);

  useEffect(() => {
    if (selectionAnchorRef.current && !items.some(item => item.id === selectionAnchorRef.current)) {
      selectionAnchorRef.current = null;
    }
  }, [items]);

  useEffect(() => {
    if (!shareMenu) return;
    const closeOnOutsidePointer = (event: globalThis.MouseEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(".folder-share-menu, .folder-share-trigger")) return;
      setShareMenu(null);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setShareMenu(null);
    };
    const closeOnViewportChange = () => setShareMenu(null);
    document.addEventListener("mousedown", closeOnOutsidePointer);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsidePointer);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [shareMenu]);

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
      if (nativeDragPrewarmTimer.current !== null) {
        clearTimeout(nativeDragPrewarmTimer.current);
        nativeDragPrewarmTimer.current = null;
      }
    };
  }, []);

  function folderShareTrigger(item: Asset) {
    if (
      item.kind !== "folder"
      || !reviewLinkShareIds
      || !onCopyReviewLink
      || !onRefreshReviewLink
    ) return null;
    const externalSourceId = item.external_source_id
      || activeExternalSourceId
      || path.at(-1)?.external_source_id;
    if (!externalSourceId) return null;
    const shareId = reviewLinkShareIds.get(
      externalSourceId + ":" + item.id,
    );
    if (!shareId) return null;
    const open = shareMenu?.shareId === shareId && shareMenu.item.id === item.id;
    return <button
      type="button"
      className="folder-share-trigger"
      aria-label={"Shared link actions for " + item.name}
      title="Shared link actions"
      aria-haspopup="menu"
      aria-expanded={open}
      onDoubleClick={event => event.stopPropagation()}
      onClick={event => {
        event.stopPropagation();
        if (open) {
          setShareMenu(null);
          return;
        }
        const rect = event.currentTarget.getBoundingClientRect();
        const menuWidth = 260;
        const menuHeight = 90;
        const left = Math.max(
          8,
          Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8),
        );
        const below = rect.bottom + 6;
        const top = below + menuHeight <= window.innerHeight - 8
          ? below
          : Math.max(8, rect.top - menuHeight - 6);
        setShareMenu({ shareId, item, left, top });
      }}
    ><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="4" cy="10" r="1.5" /><circle cx="10" cy="10" r="1.5" /><circle cx="16" cy="10" r="1.5" /></svg></button>;
  }

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
    if (!active.moved && event.target === event.currentTarget && !event.ctrlKey && !event.metaKey) {
      selectionAnchorRef.current = null;
      onReplaceSelection([]);
    }
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

  function dragItemsFor(item: Asset): Asset[] {
    if (item.kind === "folder") return [];
    return selected.has(item.id)
      ? items.filter(candidate => selected.has(candidate.id) && candidate.kind !== "folder")
      : [item];
  }

  function cancelNativeOriginalPrewarm() {
    if (nativeDragPrewarmTimer.current === null) return;
    clearTimeout(nativeDragPrewarmTimer.current);
    nativeDragPrewarmTimer.current = null;
  }

  function pruneNativeDragTickets() {
    const now = Date.now();
    for (const [key, value] of nativeDragTickets.current) {
      if (value.expiresAt <= now) nativeDragTickets.current.delete(key);
    }
  }

  function prepareNativeOriginalDrag(dragItems: Asset[]): Promise<void> {
    const desktop = window.camDesktop?.nativeDrag;
    if (!desktop || !dragItems.length) return Promise.resolve();
    const descriptors = nativeOriginalDragItems(dragItems);
    const key = nativeOriginalDragKey(descriptors);
    pruneNativeDragTickets();
    const ready = nativeDragTickets.current.get(key);
    if (ready && ready.expiresAt > Date.now() + 250) return Promise.resolve();
    if (ready) nativeDragTickets.current.delete(key);
    const inflight = nativeDragPreparing.current.get(key);
    if (inflight) return inflight;
    const task = desktop.prepare(descriptors)
      .then(result => {
        nativeDragTickets.current.set(key, {
          ticket: result.ticket,
          expiresAt: result.expiresAt,
        });
      })
      .catch(() => undefined)
      .finally(() => nativeDragPreparing.current.delete(key));
    nativeDragPreparing.current.set(key, task);
    return task;
  }

  function scheduleNativeOriginalPrewarm(item: Asset) {
    cancelNativeOriginalPrewarm();
    if (!window.camDesktop?.nativeDrag) return;
    const allDragItems = dragItemsFor(item);
    const prewarmItems = nativeOriginalPrewarmItems(allDragItems, item.id);
    if (!prewarmItems.length || prewarmItems.length !== allDragItems.length) return;
    nativeDragPrewarmTimer.current = setTimeout(() => {
      nativeDragPrewarmTimer.current = null;
      void prepareNativeOriginalDrag(prewarmItems);
    }, 100);
  }

  function dragOriginalFiles(event: DragEvent<HTMLElement>, item: Asset) {
    cancelNativeOriginalPrewarm();
    marqueeRef.current = null;
    setMarquee(null);
    const dragItems = dragItemsFor(item);
    if (!dragItems.length) {
      event.preventDefault();
      return;
    }

    const desktop = window.camDesktop?.nativeDrag;
    if (desktop) {
      const descriptors = nativeOriginalDragItems(dragItems);
      const key = nativeOriginalDragKey(descriptors);
      pruneNativeDragTickets();
      const ready = nativeDragTickets.current.get(key);
      const readyNow = Boolean(ready && ready.expiresAt > Date.now());
      const mode = nativeOriginalDragMode(true, readyNow, typeof desktop.startItems === "function");

      if (mode === "prepared" && ready) {
        event.preventDefault();
        nativeDragTickets.current.delete(key);
        desktop.start(ready.ticket);
        return;
      }

      if (mode === "direct" && desktop.startItems) {
        // Native Windows applications expect real filesystem paths. If the
        // pointer/hover prewarm has not completed yet, keep this as a native
        // drag and let the desktop shell finish materializing the originals.
        event.preventDefault();
        void desktop.startItems(descriptors).catch(() => undefined);
        return;
      }

      if (mode === "deferred") {
        // Compatibility path for desktop 0.1.9 and earlier ticket-based shells:
        // cancel Chromium's URL drag, finish materializing the real file, then
        // start the native OS drag with the ticket that prepare() issued.
        event.preventDefault();
        void prepareNativeOriginalDrag(dragItems).then(() => {
          pruneNativeDragTickets();
          const prepared = nativeDragTickets.current.get(key);
          if (!prepared || prepared.expiresAt <= Date.now()) return;
          nativeDragTickets.current.delete(key);
          desktop.start(prepared.ticket);
        });
        return;
      }
    }

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
      onPointerDown={() => {
        cancelNativeOriginalPrewarm();
        if (item.kind === "folder") onPrefetchNow?.(item.id);
        else void prepareNativeOriginalDrag(dragItemsFor(item));
      }}
      onPointerUp={cancelNativeOriginalPrewarm}
      onPointerCancel={cancelNativeOriginalPrewarm}
      onDragStart={event => dragOriginalFiles(event, item)}
      onClick={event => {
        const next = explorerSelectionForClick(
          items.map(candidate => candidate.id),
          selected,
          item.id,
          selectionAnchorRef.current,
          event,
        );
        selectionAnchorRef.current = next.anchorId;
        onReplaceSelection(next.selected);
        onFocus(item);
      }}
      onContextMenu={event => {
        if (!selected.has(item.id)) {
          selectionAnchorRef.current = item.id;
          onReplaceSelection([item.id]);
        }
        onContextMenu(item, event);
      }}
      onPointerEnter={() => {
        if (item.kind === "folder") onPrefetch(item.id);
        else scheduleNativeOriginalPrewarm(item);
      }}
      onPointerLeave={() => {
        cancelNativeOriginalPrewarm();
        onCancelPrefetch();
      }}
    >
      {onFindSimilar && item.kind === "image" && item.internal_asset_id && <button type="button" className="asset-find-similar" onClick={event => { event.stopPropagation(); onFindSimilar(item); }} aria-label={"Find similar images to " + item.name} title="Find similar images"><VisualSearchIcon /></button>}
      <button className="asset-info" onClick={event => { event.stopPropagation(); onDetails(item); }} aria-label={"View details for " + item.name}>i</button>
      <button className="check" onClick={event => { event.stopPropagation(); selectionAnchorRef.current = item.id; onToggle(item.id); }}>{selected.has(item.id) ? "✓" : ""}</button>
      <button className={"preview " + item.kind} onDoubleClick={() => openItem(item)}>
        <AssetPreview item={item} fetchPriority={thumbnailFetchPriority(index)} />
        {item.kind === "video" && <span className="video-thumbnail-badge" aria-hidden="true">▶</span>}
      </button>
      <div>
        <div className="asset-name-row">
          <button className="name" onDoubleClick={() => openItem(item)}>{item.name}</button>
          {folderShareTrigger(item)}
        </div>
        <small>{fileTypeLabel(getFileType(item.mime_type, item.kind, item.name))}{item.modified_at && item.kind !== "folder" ? " - " + new Date(item.modified_at).toLocaleDateString() : ""}</small>
        <AssetMetadataBar item={item} metadata={metadataByItem[item.id]} onRate={onRate} />
      </div>
    </article>)}
    {shareMenu && createPortal(<div
      className="folder-share-menu"
      role="menu"
      aria-label={"Shared link actions for " + shareMenu.item.name}
      style={{ left: shareMenu.left, top: shareMenu.top }}
    >
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const current = shareMenu;
          setShareMenu(null);
          void onCopyReviewLink?.(current.shareId, current.item);
        }}
      >Sao chép đường dẫn chia sẻ</button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const current = shareMenu;
          setShareMenu(null);
          void onRefreshReviewLink?.(current.shareId, current.item);
        }}
      >Cập nhật đường dẫn chia sẻ</button>
    </div>, document.body)}
  </div>;
}