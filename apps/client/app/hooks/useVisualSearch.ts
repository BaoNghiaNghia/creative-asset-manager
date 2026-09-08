import { useCallback, useRef, useState } from "react";
import type { Asset, Provider } from "../types";

export type VisualCrop = { x: number; y: number; width: number; height: number };
type VisualResponse = { query_kind: "asset" | "upload"; items: Asset[]; next_cursor?: string | null; has_more?: boolean };
type VisualReference = { kind: "asset"; asset: Asset } | { kind: "upload"; file: File; previewUrl: string };

export function visualErrorMessage(payload: unknown, fallback = "Visual search is unavailable."): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: unknown }).detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    return (detail as { message: string }).message;
  }
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}

export function normalizeCrop(crop: VisualCrop): VisualCrop {
  const x = Math.max(0, Math.min(1, crop.x));
  const y = Math.max(0, Math.min(1, crop.y));
  const width = Math.max(0.01, Math.min(1 - x, crop.width));
  const height = Math.max(0.01, Math.min(1 - y, crop.height));
  return { x, y, width, height };
}

async function responseOrError(response: Response): Promise<VisualResponse> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw Error(visualErrorMessage(payload));
  return payload as VisualResponse;
}

export function useVisualSearch(provider: Provider | null, externalSourceId: string | null) {
  const [reference, setReference] = useState<VisualReference | null>(null);
  const [items, setItems] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [refinement, setRefinement] = useState("");
  const requestRef = useRef(0);

  const clear = useCallback(() => {
    requestRef.current += 1;
    setReference(current => {
      if (current?.kind === "upload") URL.revokeObjectURL(current.previewUrl);
      return null;
    });
    setItems([]); setError(""); setLoading(false); setNextCursor(null); setRefinement("");
  }, []);

  const run = useCallback(async (
    nextReference: VisualReference,
    crop?: VisualCrop,
    cursor?: string | null,
    text?: string,
  ) => {
    const epoch = ++requestRef.current;
    setLoading(true); setError("");
    const refinedText = (text ?? refinement).trim();
    try {
      let response: Response;
      if (nextReference.kind === "asset") {
        const assetId = nextReference.asset.internal_asset_id;
        if (!assetId) throw Error("This asset is not ready for visual search.");
        response = await fetch("/api/v1/search/visual/by-asset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            asset_id: assetId,
            ...(provider ? { source_provider: provider } : {}),
            ...(externalSourceId ? { external_source_id: externalSourceId } : {}),
            ...(crop ? { crop: normalizeCrop(crop) } : {}),
            ...(cursor ? { cursor } : {}),
            ...(refinedText ? { text: refinedText } : {}),
          }),
        });
      } else {
        const params = new URLSearchParams({
          ...(provider ? { source_provider: provider } : {}),
          ...(externalSourceId ? { external_source_id: externalSourceId } : {}),
          ...(crop ? { crop: JSON.stringify(normalizeCrop(crop)) } : {}),
          ...(cursor ? { cursor } : {}),
          ...(refinedText ? { text: refinedText } : {}),
        });
        const data = new FormData();
        data.append("file", nextReference.file);
        response = await fetch("/api/v1/search/visual/upload?" + params, { method: "POST", body: data });
      }
      const payload = await responseOrError(response);
      if (epoch !== requestRef.current) return;
      setReference(nextReference);
      setItems(current => cursor ? [...current, ...payload.items.filter(item => !current.some(existing => (existing.internal_asset_id || existing.id) === (item.internal_asset_id || item.id)))] : payload.items);
      setNextCursor(payload.next_cursor || null);
    } catch (reason) {
      if (epoch === requestRef.current) setError(reason instanceof Error ? reason.message : "Visual search is unavailable.");
    } finally {
      if (epoch === requestRef.current) setLoading(false);
    }
  }, [externalSourceId, provider, refinement]);

  const chooseAsset = useCallback((asset: Asset, crop?: VisualCrop) => {
    const next = { kind: "asset" as const, asset };
    setReference(next); setRefinement("");
    void run(next, crop, undefined, "");
  }, [run]);

  const chooseUpload = useCallback((file: File, crop?: VisualCrop) => {
    const next = { kind: "upload" as const, file, previewUrl: URL.createObjectURL(file) };
    setReference(current => {
      if (current?.kind === "upload") URL.revokeObjectURL(current.previewUrl);
      return next;
    });
    setRefinement("");
    void run(next, crop, undefined, "");
  }, [run]);

  const retry = useCallback((crop?: VisualCrop, text?: string) => {
    if (reference) void run(reference, crop, undefined, text);
  }, [reference, run]);

  const loadMore = useCallback((crop?: VisualCrop) => {
    if (!reference || !nextCursor || loading) return;
    void run(reference, crop, nextCursor);
  }, [loading, nextCursor, reference, run]);

  return { reference, items, loading, error, refinement, setRefinement, hasMore: Boolean(nextCursor), chooseAsset, chooseUpload, retry, loadMore, clear };
}
