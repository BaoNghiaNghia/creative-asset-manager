import type { Asset } from "./api";

export const REVIEW_HISTORY_TTL_MS = 15 * 24 * 60 * 60 * 1000;
export const REVIEW_HISTORY_MAX_ITEMS = 100;
const REVIEW_HISTORY_PREFIX = "creative-assets:view-history:v1:";

export type ReviewHistoryEntry = {
  asset_id: string;
  source_asset_id: string;
  filename: string;
  media_type: string | null;
  thumbnail_url: string;
  preview_url: string;
  annotation_count?: number;
  folder_id?: string | null;
  viewed_at: number;
};

function historyKey(publicId: string) {
  return REVIEW_HISTORY_PREFIX + publicId;
}

function isEntry(value: unknown): value is ReviewHistoryEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<ReviewHistoryEntry>;
  return typeof entry.asset_id === "string"
    && typeof entry.source_asset_id === "string"
    && typeof entry.filename === "string"
    && (entry.media_type === null || typeof entry.media_type === "string")
    && typeof entry.thumbnail_url === "string"
    && typeof entry.preview_url === "string"
    && typeof entry.viewed_at === "number"
    && Number.isFinite(entry.viewed_at);
}

export function pruneReviewHistory(entries: ReviewHistoryEntry[], now = Date.now()) {
  const cutoff = now - REVIEW_HISTORY_TTL_MS;
  const deduped = new Map<string, ReviewHistoryEntry>();
  for (const entry of entries) {
    if (!isEntry(entry) || entry.viewed_at < cutoff || entry.viewed_at > now + 60_000) continue;
    const key = entry.asset_id + ":" + entry.source_asset_id;
    const existing = deduped.get(key);
    if (!existing || entry.viewed_at > existing.viewed_at) deduped.set(key, entry);
  }
  return [...deduped.values()]
    .sort((a, b) => b.viewed_at - a.viewed_at)
    .slice(0, REVIEW_HISTORY_MAX_ITEMS);
}

export function readReviewHistory(publicId: string, now = Date.now()): ReviewHistoryEntry[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(historyKey(publicId));
    const parsed = raw ? JSON.parse(raw) : [];
    const entries = pruneReviewHistory(Array.isArray(parsed) ? parsed : [], now);
    localStorage.setItem(historyKey(publicId), JSON.stringify(entries));
    return entries;
  } catch {
    return [];
  }
}

export function recordReviewHistory(publicId: string, asset: Asset, folderId?: string | null, now = Date.now()) {
  const current = readReviewHistory(publicId, now);
  const key = asset.asset_id + ":" + asset.source_asset_id;
  const next = pruneReviewHistory([
    {
      asset_id: asset.asset_id,
      source_asset_id: asset.source_asset_id,
      filename: asset.filename,
      media_type: asset.media_type,
      thumbnail_url: asset.thumbnail_url,
      preview_url: asset.preview_url,
      annotation_count: asset.annotation_count,
      folder_id: folderId ?? null,
      viewed_at: now,
    },
    ...current.filter(entry => entry.asset_id + ":" + entry.source_asset_id !== key),
  ], now);
  if (typeof localStorage !== "undefined") {
    try { localStorage.setItem(historyKey(publicId), JSON.stringify(next)); } catch { /* local-only history is best effort */ }
  }
  return next;
}

export function clearReviewHistory(publicId: string) {
  if (typeof localStorage === "undefined") return;
  try { localStorage.removeItem(historyKey(publicId)); } catch { /* no-op */ }
}

export function historyEntryAsset(entry: ReviewHistoryEntry): Asset {
  return {
    kind: "asset",
    asset_id: entry.asset_id,
    source_asset_id: entry.source_asset_id,
    filename: entry.filename,
    media_type: entry.media_type,
    thumbnail_url: entry.thumbnail_url,
    preview_url: entry.preview_url,
    annotation_count: entry.annotation_count,
  };
}
