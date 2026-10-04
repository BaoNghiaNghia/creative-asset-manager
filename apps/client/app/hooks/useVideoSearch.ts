import { useCallback, useEffect, useRef, useState } from "react";
import type { Provider } from "../types";
import { searchApiErrorMessage } from "./useSearchV3";

export type VideoSearchMatch = { start_ms: number; end_ms: number; summary: string; visual_description: string; speech: string; confidence: number; score: number; };
export type VideoProcessingStep = { key: string; label: string; status: string; attempt_count: number; max_attempts: number; updated_at: string | null; error_code: string | null; };
export type VideoSearchItem = { source_asset_id: string; analysis_run_id: string; filename: string; mime_type: string; duration_ms: number | null; source_type: string | null; external_source_id: string | null; external_asset_id: string | null; web_url: string | null; thumbnail_url: string | null; score: number; best_match: VideoSearchMatch; matches: VideoSearchMatch[]; steps?: VideoProcessingStep[]; };
export type VideoSearchResponse = { items: VideoSearchItem[]; total: number; took_ms: number | null };
export type VideoSearchConfig = {
  authenticated: boolean;
  enabled: boolean;
  query: string;
  provider: Provider;
  externalSourceId: string | null;
  designTypes: string[];
};
export const VIDEO_SEARCH_LIMIT = 10;

export function isCurrentVideoSearchResponse(requestEpoch: number, currentEpoch: number): boolean {
  return requestEpoch === currentEpoch;
}

export function videoSearchErrorMessage(status: number, payload: unknown): string {
  if (status === 422) return "Enter a valid video search query.";
  if (status === 502) return "Video search returned an invalid response. Please try again.";
  if (status === 503) return "Video search is temporarily unavailable. Please try again later.";
  return searchApiErrorMessage(payload, "Video search failed. Please try again.");
}

export function parseVideoSearchResponse(value: unknown): VideoSearchResponse {
  const payload = value as Partial<VideoSearchResponse> | null;
  return {
    items: Array.isArray(payload?.items) ? payload.items : [],
    total: typeof payload?.total === "number" ? payload.total : 0,
    took_ms: typeof payload?.took_ms === "number" ? payload.took_ms : null,
  };
}

export function mergeVideoSearchItems(current: VideoSearchItem[], incoming: VideoSearchItem[]): VideoSearchItem[] {
  const merged = [...current];
  const seen = new Set(current.map(item => item.source_asset_id + ":" + item.analysis_run_id));
  for (const item of incoming) {
    const key = item.source_asset_id + ":" + item.analysis_run_id;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(item);
  }
  return merged;
}

export function buildVideoSearchRequestBody(
  query: string,
  offset: number,
  externalSourceId: string | null,
  designTypes: string[],
) {
  return {
    query: query.trim(),
    limit: VIDEO_SEARCH_LIMIT,
    offset,
    ...(externalSourceId ? { external_source_id: externalSourceId } : {}),
    ...(designTypes.length ? { design_types: designTypes } : {}),
  };
}

export function useVideoSearch({
  authenticated,
  enabled,
  query,
  provider,
  externalSourceId,
  designTypes,
}: VideoSearchConfig) {
  const [result, setResult] = useState<VideoSearchResponse>({ items: [], total: 0, took_ms: null });
  const designTypesKey = designTypes.join("\u001f");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(0);
  const [error, setError] = useState("");
  const epoch = useRef(0);
  const appendController = useRef<AbortController | null>(null);
  const pageInFlight = useRef(false);

  const fetchPage = useCallback(async (
    offset: number,
    append: boolean,
    requestEpoch: number,
    signal: AbortSignal,
  ) => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) return;
    if (append) setLoadingMore(true); else setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/v1/search/video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal,
        body: JSON.stringify(buildVideoSearchRequestBody(
          normalizedQuery,
          offset,
          externalSourceId,
          designTypesKey ? designTypesKey.split("\u001f") : [],
        )),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw Object.assign(
          new Error(videoSearchErrorMessage(response.status, payload)),
          { status: response.status },
        );
      }
      if (signal.aborted || !isCurrentVideoSearchResponse(requestEpoch, epoch.current)) return;
      const parsed = parseVideoSearchResponse(payload);
      setResult(current => append
        ? { ...parsed, items: mergeVideoSearchItems(current.items, parsed.items) }
        : parsed);
      setNextOffset(Math.min(parsed.total, offset + VIDEO_SEARCH_LIMIT));
    } catch (reason) {
      if (!signal.aborted && isCurrentVideoSearchResponse(requestEpoch, epoch.current)) {
        setError(reason instanceof Error ? reason.message : "Video search failed. Please try again.");
      }
    } finally {
      if (isCurrentVideoSearchResponse(requestEpoch, epoch.current)) {
        if (append) setLoadingMore(false); else setLoading(false);
      }
      if (append) pageInFlight.current = false;
    }
  }, [query, externalSourceId, designTypesKey]);

  useEffect(() => {
    const requestEpoch = ++epoch.current;
    appendController.current?.abort();
    appendController.current = null;
    pageInFlight.current = false;
    setLoadingMore(false);
    setNextOffset(0);
    const normalizedQuery = query.trim();
    if (!enabled || !authenticated || !normalizedQuery) {
      setResult({ items: [], total: 0, took_ms: null });
      setLoading(false);
      setError("");
      return;
    }

    setResult({ items: [], total: 0, took_ms: null });
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetchPage(0, false, requestEpoch, controller.signal);
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      appendController.current?.abort();
    };
  }, [authenticated, enabled, query, provider, externalSourceId, designTypesKey, fetchPage]);

  const hasMore = nextOffset < result.total;
  const loadMore = useCallback(() => {
    if (
      !enabled
      || !authenticated
      || !query.trim()
      || !hasMore
      || loading
      || loadingMore
      || pageInFlight.current
    ) return;
    pageInFlight.current = true;
    const controller = new AbortController();
    appendController.current?.abort();
    appendController.current = controller;
    void fetchPage(nextOffset, true, epoch.current, controller.signal);
  }, [authenticated, enabled, fetchPage, hasMore, loading, loadingMore, nextOffset, query]);

  return { ...result, loading, loadingMore, hasMore, loadMore, error };
}
