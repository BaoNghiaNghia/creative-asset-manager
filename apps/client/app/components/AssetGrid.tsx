import { useState } from "react";
import type { Asset, AssetMetadata, AssetMetadataMap } from "../types";

const icons = { folder: "📁", image: "▧", video: "▶", pdf: "PDF", document: "DOC", other: "◇" };

function AssetPreview({ item }: { item: Asset }) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const canShowThumbnail = (item.kind === "image" || item.kind === "video")
    && Boolean(item.thumbnail_url)
    && !thumbnailFailed;

  if (!canShowThumbnail) {
    return <span className="preview-fallback">{icons[item.kind]}</span>;
  }

  return <>
    <img
      className="preview-thumbnail"
      src={item.thumbnail_url}
      alt=""
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setThumbnailFailed(true)}
    />
    {item.kind === "video" && <span className="video-thumbnail-badge" aria-hidden="true">▶</span>}
  </>;
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
    {visibility && <span className={"asset-status " + visibility}>{visibility}</span>}
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
      onPointerEnter={() => item.kind === "folder" && onPrefetch(item.id)}
      onPointerLeave={onCancelPrefetch}
    >
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
