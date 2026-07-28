import { useEffect, useRef, useState } from "react";
import type { Asset, AssetMetadata, AssetMetadataMap } from "../types";
import { AssetStatusBadge } from "./AssetStatusBadge";

const icons = { folder: "📁", image: "▧", video: "▶", pdf: "PDF", document: "DOC", other: "◇" };

export function shouldLoadAssetThumbnail(inViewport: boolean, thumbnailUrl?: string | null): boolean {
  return inViewport && Boolean(thumbnailUrl);
}

function AssetPreview({ item }: { item: Asset }) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const [thumbnailLoaded, setThumbnailLoaded] = useState(false);
  const [inViewport, setInViewport] = useState(false);
  const previewRef = useRef<HTMLSpanElement>(null);
  const canShowThumbnail = (item.kind === "image" || item.kind === "video")
    && Boolean(item.thumbnail_url)
    && !thumbnailFailed;

  useEffect(() => {
    setThumbnailFailed(false);
    setThumbnailLoaded(false);
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
    }, { rootMargin: "480px 0px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, [canShowThumbnail]);

  if (!canShowThumbnail) {
    return <span className="preview-fallback">{icons[item.kind]}</span>;
  }

  const shouldLoad = shouldLoadAssetThumbnail(inViewport, item.thumbnail_url);
  return <span ref={previewRef} className="thumbnail-frame">
    {!thumbnailLoaded && <span className="thumbnail-skeleton" aria-hidden="true" />}
    {shouldLoad && <img
      className={"preview-thumbnail" + (thumbnailLoaded ? " is-loaded" : "")}
      src={item.thumbnail_url}
      alt=""
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onLoad={() => setThumbnailLoaded(true)}
      onError={() => setThumbnailFailed(true)}
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
    {items.map(item => <article
      className={selected.has(item.id) ? "selected" : ""}
      key={item.id}
      onClick={() => onFocus(item)}
      onPointerEnter={() => item.kind === "folder" && onPrefetch(item.id)}
      onPointerLeave={onCancelPrefetch}
    >
      <button className="asset-info" onClick={event => { event.stopPropagation(); onDetails(item); }} aria-label={"View details for " + item.name}>i</button>
      <button className="check" onClick={() => onToggle(item.id)}>{selected.has(item.id) ? "✓" : ""}</button>
      <button className={"preview " + item.kind} onDoubleClick={() => openItem(item)}>
        <AssetPreview item={item} />
      </button>
      <div>
        <button className="name" onDoubleClick={() => openItem(item)}>{item.name}</button>
        <small>{item.kind === "folder" ? "Folder" : item.modified_at ? new Date(item.modified_at).toLocaleDateString() : item.mime_type}</small>
        <AssetMetadataBar item={item} metadata={metadataByItem[item.id]} onRate={onRate} />
      </div>
    </article>)}
  </div>;
}
