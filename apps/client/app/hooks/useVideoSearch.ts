import { useEffect, useRef, useState } from "react";
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
};
export const VIDEO_SEARCH_LIMIT = 20;

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

export function useVideoSearch({
  authenticated,
  enabled,
  query,
  provider,
  externalSourceId,
}: VideoSearchConfig) {
  const [result, setResult] = useState<VideoSearchResponse>({ items: [], total: 0, took_ms: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const epoch = useRef(0);

  useEffect(() => {
    const requestEpoch = ++epoch.current;
    const normalizedQuery = query.trim();
    if (!enabled || !authenticated || !normalizedQuery) {
      setResult({ items: [], total: 0, took_ms: null });
      setLoading(false);
      setError("");
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const response = await fetch("/api/v1/search/video", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            query: normalizedQuery,
            limit: VIDEO_SEARCH_LIMIT,
            ...(externalSourceId ? { external_source_id: externalSourceId } : {}),
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw Object.assign(
            new Error(videoSearchErrorMessage(response.status, payload)),
            { status: response.status },
          );
        }
        if (controller.signal.aborted || !isCurrentVideoSearchResponse(requestEpoch, epoch.current)) return;
        setResult(parseVideoSearchResponse(payload));
      } catch (reason) {
        if (!controller.signal.aborted && isCurrentVideoSearchResponse(requestEpoch, epoch.current)) {
          setError(reason instanceof Error ? reason.message : "Video search failed. Please try again.");
        }
      } finally {
        if (!controller.signal.aborted && isCurrentVideoSearchResponse(requestEpoch, epoch.current)) {
          setLoading(false);
        }
      }
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [authenticated, enabled, query, provider, externalSourceId]);

  return { ...result, loading, error };
}
