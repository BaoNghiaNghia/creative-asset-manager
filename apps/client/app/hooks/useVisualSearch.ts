import { useCallback, useRef, useState } from "react";
import type { Asset, Provider } from "../types";

export type VisualCrop = { x: number; y: number; width: number; height: number };
type VisualResponse = { query_kind: "asset" | "upload"; items: Asset[]; next_cursor?: string | null };
type VisualReference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string };
export type CommittedVisualQuery = { crop?: VisualCrop; text: string; provider: Provider | null; externalSourceId: string | null };

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
export function committedVisualQuery(crop: VisualCrop | undefined, text: string, provider: Provider | null, externalSourceId: string | null): CommittedVisualQuery {
  return { crop: crop ? normalizeCrop(crop) : undefined, text: text.trim(), provider, externalSourceId };
}
async function responseOrError(response: Response): Promise<VisualResponse> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw Error(visualErrorMessage(payload));
  return payload as VisualResponse;
}

export function useVisualSearch(provider: Provider | null, externalSourceId: string | null) {
  const [reference, setReference] = useState<VisualReference | null>(null);
  const [items, setItems] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(false), [error, setError] = useState(""), [nextCursor, setNextCursor] = useState<string | null>(null);
  const [refinement, setRefinement] = useState(""), [committedQuery, setCommittedQuery] = useState<CommittedVisualQuery | null>(null);
  const requestRef = useRef(0), committedReferenceRef = useRef<VisualReference | null>(null), committedQueryRef = useRef<CommittedVisualQuery | null>(null);

  const clear = useCallback(() => {
    requestRef.current += 1;
    setReference(current => { if (current?.kind === "upload") URL.revokeObjectURL(current.previewUrl); return null; });
    committedReferenceRef.current = null; committedQueryRef.current = null;
    setItems([]); setError(""); setLoading(false); setNextCursor(null); setRefinement(""); setCommittedQuery(null);
  }, []);

  const run = useCallback(async (nextReference: VisualReference, query: CommittedVisualQuery, cursor?: string | null) => {
    const epoch = ++requestRef.current, append = Boolean(cursor); setLoading(true); setError("");
    try {
      let response: Response;
      if (nextReference.kind === "asset") {
        const assetId = nextReference.asset.internal_asset_id;
        if (!assetId) throw Error("This asset is not ready for visual search.");
        response = await fetch("/api/v1/search/visual/by-asset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_id: assetId, ...(query.provider ? { source_provider: query.provider } : {}), ...(query.externalSourceId ? { external_source_id: query.externalSourceId } : {}), ...(query.crop ? { crop: query.crop } : {}), ...(cursor ? { cursor } : {}), ...(query.text ? { text: query.text } : {}) }) });
      } else {
        const params = new URLSearchParams({ ...(query.provider ? { source_provider: query.provider } : {}), ...(query.externalSourceId ? { external_source_id: query.externalSourceId } : {}), ...(query.crop ? { crop: JSON.stringify(query.crop) } : {}), ...(cursor ? { cursor } : {}), ...(query.text ? { text: query.text } : {}) });
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

  const start = useCallback((nextReference: VisualReference, crop?: VisualCrop, text = "") => void run(nextReference, committedVisualQuery(crop, text, provider, externalSourceId)), [externalSourceId, provider, run]);
  const chooseAsset = useCallback((asset: Asset, crop?: VisualCrop) => { const next = { kind: "asset" as const, asset }; setReference(next); setRefinement(""); start(next, crop); }, [start]);
  const chooseUpload = useCallback((file: File, crop?: VisualCrop) => { const next = { kind: "upload" as const, file, previewUrl: URL.createObjectURL(file) }; setReference(current => { if (current?.kind === "upload") URL.revokeObjectURL(current.previewUrl); return next; }); setRefinement(""); start(next, crop); }, [start]);
  const retry = useCallback((crop?: VisualCrop, text?: string) => { if (reference) start(reference, crop, text ?? refinement); }, [reference, refinement, start]);
  const loadMore = useCallback(() => { const activeReference = committedReferenceRef.current, activeQuery = committedQueryRef.current; if (!activeReference || !activeQuery || !nextCursor || loading) return; void run(activeReference, activeQuery, nextCursor); }, [loading, nextCursor, run]);
  return { reference, items, loading, error, refinement, setRefinement, committedQuery, hasMore: Boolean(nextCursor), chooseAsset, chooseUpload, retry, loadMore, clear };
}
