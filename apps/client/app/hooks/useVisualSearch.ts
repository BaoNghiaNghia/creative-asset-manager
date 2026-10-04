import { useCallback, useEffect, useRef, useState } from "react";
import type { Asset, Provider } from "../types";
import { prepareVisualSearchUpload } from "../utils/visualUpload";

export type VisualCrop = { x: number; y: number; width: number; height: number };
type VisualResponse = { query_kind: "asset" | "upload"; items: Asset[]; next_cursor?: string | null };
type VisualReference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string; prepared: boolean };
export type VisualSearchScope = "all" | "source" | "folder";
export const DEFAULT_VISUAL_SEARCH_SCOPE: VisualSearchScope = "all";
export function defaultVisualSearchScope(canSearchAllResources: boolean | null, hasCurrentSource: boolean, hasCurrentFolder: boolean): VisualSearchScope | null {
  if (canSearchAllResources === null) return null;
  if (canSearchAllResources) return "all";
  if (hasCurrentFolder) return "folder";
  return hasCurrentSource ? "source" : null;
}
export type CommittedVisualQuery = { crop?: VisualCrop; text: string; scope: VisualSearchScope; provider: Provider | null; externalSourceId: string | null; folderId: string | null };

export function visualErrorMessage(payload: unknown, fallback = "Visual search is unavailable."): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: unknown }).detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") return (detail as { message: string }).message;
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}
export function normalizeCrop(crop: VisualCrop): VisualCrop {
  const x = Math.max(0, Math.min(1, crop.x)), y = Math.max(0, Math.min(1, crop.y));
  return { x, y, width: Math.max(0.01, Math.min(1 - x, crop.width)), height: Math.max(0.01, Math.min(1 - y, crop.height)) };
}
export function committedVisualQuery(crop: VisualCrop | undefined, text: string, scope: VisualSearchScope, provider: Provider | null, externalSourceId: string | null, folderId: string | null): CommittedVisualQuery {
  return { crop: crop ? normalizeCrop(crop) : undefined, text: text.trim(), scope, provider: scope === "all" ? null : provider, externalSourceId: scope === "all" ? null : externalSourceId, folderId: scope === "folder" ? folderId : null };
}
export function visualByAssetRequest(assetId: string, query: CommittedVisualQuery, cursor?: string | null): Record<string, unknown> {
  return { asset_id: assetId, ...(query.provider ? { source_provider: query.provider } : {}), scope: query.scope, ...(query.externalSourceId ? { external_source_id: query.externalSourceId } : {}), ...(query.folderId ? { folder_id: query.folderId } : {}), ...(query.crop ? { crop: query.crop } : {}), ...(cursor ? { cursor } : {}), ...(query.text ? { text: query.text } : {}) };
}
export function visualUploadParams(query: CommittedVisualQuery, cursor?: string | null): URLSearchParams {
  return new URLSearchParams({ ...(query.provider ? { source_provider: query.provider } : {}), scope: query.scope, ...(query.externalSourceId ? { external_source_id: query.externalSourceId } : {}), ...(query.folderId ? { folder_id: query.folderId } : {}), ...(query.crop ? { crop: JSON.stringify(query.crop) } : {}), ...(cursor ? { cursor } : {}), ...(query.text ? { text: query.text } : {}) });
}
async function responseOrError(response: Response): Promise<VisualResponse> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw Error(visualErrorMessage(payload));
  return payload as VisualResponse;
}

export function useVisualSearch(provider: Provider | null, externalSourceId: string | null, folderId: string | null, canSearchAllResources: boolean | null) {
  const hasCurrentSource = Boolean(provider && externalSourceId);
  const hasCurrentFolder = hasCurrentSource && Boolean(folderId);
  const safeDefaultScope = defaultVisualSearchScope(canSearchAllResources, hasCurrentSource, hasCurrentFolder);
  const [reference, setReference] = useState<VisualReference | null>(null);
  const [items, setItems] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(false), [preparingUpload, setPreparingUpload] = useState(false), [error, setError] = useState(""), [nextCursor, setNextCursor] = useState<string | null>(null);
  const [refinement, setRefinement] = useState(""), [scope, setScope] = useState<VisualSearchScope | null>(() => safeDefaultScope), [committedQuery, setCommittedQuery] = useState<CommittedVisualQuery | null>(null);
  const requestRef = useRef(0), committedReferenceRef = useRef<VisualReference | null>(null), committedQueryRef = useRef<CommittedVisualQuery | null>(null);

  useEffect(() => {
    if (canSearchAllResources === null) return;
    setScope(current => {
      if (current === null) return safeDefaultScope;
      if (current === "all" && !canSearchAllResources) return safeDefaultScope;
      if (current === "source" && !hasCurrentSource) return safeDefaultScope;
      if (current === "folder" && !hasCurrentFolder) return safeDefaultScope;
      return current;
    });
  }, [canSearchAllResources, hasCurrentFolder, hasCurrentSource, safeDefaultScope]);

  const clear = useCallback(() => {
    requestRef.current += 1;
    setReference(current => { if (current?.kind === "upload") URL.revokeObjectURL(current.previewUrl); return null; });
    committedReferenceRef.current = null; committedQueryRef.current = null;
    setItems([]); setError(""); setLoading(false); setPreparingUpload(false); setNextCursor(null); setRefinement(""); setCommittedQuery(null); setScope(safeDefaultScope);
  }, [safeDefaultScope]);

  const run = useCallback(async (nextReference: VisualReference, query: CommittedVisualQuery, cursor?: string | null) => {
    const epoch = ++requestRef.current, append = Boolean(cursor); setLoading(true); setError("");
    try {
      let response: Response;
      if (nextReference.kind === "asset") {
        const assetId = nextReference.asset.internal_asset_id;
        if (!assetId) throw Error("This asset is not ready for visual search.");
        response = await fetch("/api/v1/search/visual/by-asset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(visualByAssetRequest(assetId, query, cursor)) });
      } else {
        const params = visualUploadParams(query, cursor);
        const data = new FormData(); data.append("file", nextReference.file);
        response = await fetch("/api/v1/search/visual/upload?" + params, { method: "POST", body: data });
      }
      const payload = await responseOrError(response); if (epoch !== requestRef.current) return;
      setReference(nextReference);
      setItems(current => append ? [...current, ...payload.items.filter(item => !current.some(existing => (existing.internal_asset_id || existing.id) === (item.internal_asset_id || item.id)))] : payload.items);
      setNextCursor(payload.next_cursor || null);
      if (!append) { committedReferenceRef.current = nextReference; committedQueryRef.current = query; setCommittedQuery(query); }
    } catch (reason) { if (epoch === requestRef.current) setError(reason instanceof Error ? reason.message : "Visual search is unavailable."); }
    finally { if (epoch === requestRef.current) setLoading(false); }
  }, []);

  const start = useCallback((nextReference: VisualReference, crop?: VisualCrop, text = "") => {
    if (!scope) { setError("Choose an authorized source or folder before searching."); return; }
    void run(nextReference, committedVisualQuery(crop, text, scope, provider, externalSourceId, folderId));
  }, [externalSourceId, folderId, provider, run, scope]);
  const chooseAsset = useCallback((asset: Asset, crop?: VisualCrop) => { const next = { kind: "asset" as const, asset }; setReference(next); setRefinement(""); start(next, crop); }, [start]);
  const chooseUpload = useCallback((file: File, crop?: VisualCrop) => {
    if (!scope) { setError("Choose an authorized source or folder before searching."); return; }
    const epoch = ++requestRef.current;
    const previewUrl = URL.createObjectURL(file);
    const initial = { kind: "upload" as const, file, previewUrl, prepared: false };
    setReference(current => {
      if (current?.kind === "upload") URL.revokeObjectURL(current.previewUrl);
      return initial;
    });
    setRefinement("");
    setError("");
    setLoading(true);
    setPreparingUpload(true);
    void prepareVisualSearchUpload(file)
      .then(prepared => {
        if (epoch !== requestRef.current) return;
        const next = { kind: "upload" as const, file: prepared.file, previewUrl, prepared: true };
        setReference(current => current?.kind === "upload" && current.previewUrl === previewUrl ? next : current);
        setPreparingUpload(false);
        start(next, crop);
      })
      .catch(reason => {
        if (epoch !== requestRef.current) return;
        setPreparingUpload(false);
        setLoading(false);
        setError(reason instanceof Error ? reason.message : "Could not optimize this image for visual search.");
      });
  }, [scope, start]);
  const retry = useCallback((crop?: VisualCrop, text?: string) => {
    if (!reference) return;
    if (reference.kind === "upload" && !reference.prepared) {
      chooseUpload(reference.file, crop);
      return;
    }
    start(reference, crop, text ?? refinement);
  }, [chooseUpload, reference, refinement, start]);
  const loadMore = useCallback(() => { const activeReference = committedReferenceRef.current, activeQuery = committedQueryRef.current; if (!activeReference || !activeQuery || !nextCursor || loading) return; void run(activeReference, activeQuery, nextCursor); }, [loading, nextCursor, run]);
  return { reference, items, loading, preparingUpload, error, refinement, setRefinement, scope, setScope, committedQuery, hasMore: Boolean(nextCursor), chooseAsset, chooseUpload, retry, loadMore, clear };
}
