import { useCallback, useEffect, useRef, useState } from "react";
import type { Asset } from "../types";
import { explorerAssetUrl } from "../utils/mediaUrls";

// Visual query inputs belong to the authenticated user, never to the folder being browsed.
// Keep uploads as Files in IndexedDB so clicking history can actually repeat their search.
export const VISUAL_HISTORY_LIMIT = 24;
const DB_NAME = "cam-visual-search-history-v1";
const STORE_NAME = "images";

type StoredAsset = Pick<Asset, "id" | "internal_asset_id" | "external_source_id" | "provider" | "name" | "kind" | "mime_type">;
export type VisualHistoryRecord =
  | { key: string; owner: string; updatedAt: number; kind: "asset"; asset: StoredAsset }
  | { key: string; owner: string; updatedAt: number; kind: "upload"; file: File };
export type VisualHistoryEntry =
  | { key: string; kind: "asset"; asset: Asset; previewUrl: string }
  | { key: string; kind: "upload"; file: File; previewUrl: string };

function historyKey(owner: string, value: string): string {
  return JSON.stringify([owner, value]);
}

export function recentAssetRecord(owner: string, asset: Asset, updatedAt = Date.now()): VisualHistoryRecord {
  const { id, internal_asset_id, external_source_id, provider, name, kind, mime_type } = asset;
  return {
    owner, updatedAt, kind: "asset",
    key: historyKey(owner, "asset:" + provider + ":" + (external_source_id || "") + ":" + (internal_asset_id || id)),
    asset: { id, internal_asset_id, external_source_id, provider, name, kind, mime_type },
  };
}

export function recentUploadRecord(owner: string, file: File, updatedAt = Date.now()): VisualHistoryRecord {
  return {
    owner, updatedAt, kind: "upload", file,
    key: historyKey(owner, "upload:" + file.name + ":" + file.size + ":" + file.lastModified),
  };
}

export function curateVisualHistory(items: VisualHistoryRecord[], owner: string): VisualHistoryRecord[] {
  const seen = new Set<string>();
  return [...items].sort((a, b) => b.updatedAt - a.updatedAt).filter(item => {
    if (item.owner !== owner || !item.key || seen.has(item.key)) return false;
    if (item.kind === "asset") {
      if (item.asset.kind !== "image" || !item.asset.id || !item.asset.provider) return false;
    } else if (item.kind !== "upload" || !(item.file instanceof File)) return false;
    seen.add(item.key);
    return true;
  }).slice(0, VISUAL_HISTORY_LIMIT);
}

function openHistoryDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise(resolve => {
    let request: IDBOpenDBRequest;
    try { request = indexedDB.open(DB_NAME, 1); } catch { resolve(null); return; }
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME, { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

async function readHistory(owner: string): Promise<VisualHistoryRecord[]> {
  const db = await openHistoryDb();
  if (!db) return [];
  return new Promise(resolve => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve(curateVisualHistory(req.result as VisualHistoryRecord[], owner));
    req.onerror = () => resolve([]);
    tx.oncomplete = () => db.close();
    tx.onerror = () => db.close();
  });
}

async function saveHistory(entry: VisualHistoryRecord): Promise<void> {
  const db = await openHistoryDb();
  if (!db) return;
  return new Promise(resolve => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const req = store.getAll();
    req.onsuccess = () => {
      const previous = (req.result as VisualHistoryRecord[]).filter(item => item.owner === entry.owner);
      store.put(entry);
      const retained = new Set(curateVisualHistory([entry, ...previous.filter(item => item.key !== entry.key)], entry.owner).map(item => item.key));
      for (const stale of previous) if (!retained.has(stale.key)) store.delete(stale.key);
    };
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
    tx.onabort = () => { db.close(); resolve(); };
  });
}

export function useRecentVisualSearches(owner: string | null | undefined) {
  const [records, setRecords] = useState<VisualHistoryRecord[]>([]);
  const objectUrls = useRef(new Map<string, string>());
  const ownerRef = useRef(owner);
  ownerRef.current = owner;
  useEffect(() => {
    let active = true;
    for (const url of objectUrls.current.values()) URL.revokeObjectURL(url);
    objectUrls.current.clear();
    setRecords([]);
    if (owner) void readHistory(owner).then(previous => {
      if (active) setRecords(current => curateVisualHistory([...current, ...previous], owner));
    });
    return () => { active = false; };
  }, [owner]);

  useEffect(() => () => {
    for (const url of objectUrls.current.values()) URL.revokeObjectURL(url);
    objectUrls.current.clear();
  }, []);

  const remember = useCallback((entry: VisualHistoryRecord) => {
    if (!ownerRef.current || entry.owner !== ownerRef.current) return;
    setRecords(current => curateVisualHistory([entry, ...current.filter(item => item.key !== entry.key)], entry.owner));
    void saveHistory(entry);
  }, []);
  const rememberAsset = useCallback((asset: Asset) => {
    if (ownerRef.current && asset.kind === "image") remember(recentAssetRecord(ownerRef.current, asset));
  }, [remember]);
  const rememberUpload = useCallback((file: File) => {
    if (ownerRef.current) remember(recentUploadRecord(ownerRef.current, file));
  }, [remember]);

  const images: VisualHistoryEntry[] = records.filter(record => record.owner === owner).map(record => {
    if (record.kind === "asset") return {
      key: record.key, kind: "asset" as const, asset: record.asset as Asset,
      previewUrl: explorerAssetUrl(record.asset, "thumbnail"),
    };
    let previewUrl = objectUrls.current.get(record.key);
    if (!previewUrl) {
      previewUrl = URL.createObjectURL(record.file);
      objectUrls.current.set(record.key, previewUrl);
    }
    return { key: record.key, kind: "upload" as const, file: record.file, previewUrl };
  });
  return { images, rememberAsset, rememberUpload };
}
