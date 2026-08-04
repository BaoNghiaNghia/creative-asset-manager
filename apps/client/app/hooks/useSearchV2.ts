import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Asset, ParsedQueryDebug, Provider, SearchCapabilities, SearchFacetBucket, SearchSuggestion } from "../types";


export function searchApiErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (!payload || typeof payload !== "object") return fallback;
  const record = payload as { detail?: unknown; message?: unknown };
  if (typeof record.message === "string" && record.message.trim()) return record.message;
  if (typeof record.detail === "string" && record.detail.trim()) return record.detail;
  if (record.detail && typeof record.detail === "object") {
    const detail = record.detail as { message?: unknown };
    if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
  }
  return fallback;
}

const emptyCapabilities: SearchCapabilities = {
  selected_version: "v1", v2_available: false, parser_available: false,
  debug_allowed: false, facet_names: [], examples: [],
};
const SUGGESTION_DEBOUNCE_MS = 60;
const SUGGESTION_CACHE_TTL_MS = 20_000;
export const SEARCH_PAGE_SIZE = 60;

export function shouldFetchSearchSuggestions(active: boolean, authenticated: boolean, query: string): boolean {
  return active && authenticated && query.trim().length >= 2;
}

export function isSearchRequestInFlight(query: string, loading: boolean): boolean {
  return query.trim().length > 0 && loading;
}

export function mergeSearchResults(current: Asset[], incoming: Asset[]): Asset[] {
  const merged = [...current];
  const seen = new Set(current.map(item => `${item.external_source_id || item.provider}:${item.internal_asset_id || item.id}`));
  incoming.forEach(item => {
    const key = `${item.external_source_id || item.provider}:${item.internal_asset_id || item.id}`;
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(item);
    }
  });
  return merged;
}

export function isCurrentSearchResponse(requestEpoch: number, currentEpoch: number): boolean {
  return requestEpoch === currentEpoch;
}

export function buildSearchRequestBody(
  query: string,
  provider: Provider,
  facets: Record<string, string[]>,
  cursor: string | null,
  append: boolean,
  debug: boolean,
  externalSourceId?: string | null,
) {
  return {
    query: query.trim(),
    source_provider: provider,
    ...(externalSourceId ? { external_source_id: externalSourceId } : {}),
    facets,
    limit: SEARCH_PAGE_SIZE,
    ...(cursor ? { cursor } : {}),
    include_facets: !append,
    debug,
  };
}

export function useSearchV2(authenticated: boolean, provider: Provider, query: string, externalSourceId?: string | null) {
  const [capabilities, setCapabilities] = useState(emptyCapabilities);
  const [items, setItems] = useState<Asset[]>([]);
  const [facets, setFacets] = useState<Record<string, SearchFacetBucket[]>>({});
  const [selectedFacets, setSelectedFacets] = useState<Record<string, string[]>>({});
  const [parsed, setParsed] = useState<ParsedQueryDebug | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [durationMs, setDurationMs] = useState<number | null>(null);
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [error, setError] = useState("");
  const [capabilitiesResolved, setCapabilitiesResolved] = useState(false);
  const suggestionCache = useRef(new Map<string, { expiresAt: number; values: SearchSuggestion[] }>());
  const searchEpoch = useRef(0);
  const nextCursor = useRef<string | null>(null);
  const pageInFlight = useRef(false);
  const appendController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!authenticated) {
      suggestionCache.current.clear();
      setCapabilities(emptyCapabilities);
      setCapabilitiesResolved(false);
      return;
    }
    setCapabilitiesResolved(false);
    const controller = new AbortController();
    fetch("/api/v1/search/capabilities", { signal: controller.signal })
      .then(async response => {
        if (!response.ok) { const payload = await response.json().catch(() => null); throw Error(searchApiErrorMessage(payload, "Search is unavailable")); }
        setCapabilities(await response.json());
      })
      .catch(reason => !controller.signal.aborted && setError(reason.message))
      .finally(() => !controller.signal.aborted && setCapabilitiesResolved(true));
    return () => controller.abort();
  }, [authenticated, provider]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial: Record<string, string[]> = {};
    params.forEach((value, key) => {
      if (key.startsWith("facet.")) initial[key.slice(6)] = value.split(",").filter(Boolean);
    });
    setSelectedFacets(initial);
  }, []);

  const active = capabilities.selected_version === "v2" || capabilities.selected_version === "v3";
  const facetKey = JSON.stringify(selectedFacets);

  useEffect(() => {
    if (!shouldFetchSearchSuggestions(active, authenticated, query)) {
      setSuggestions([]); setSuggestionsLoading(false); return;
    }
    const normalizedQuery = query.trim();
    const cacheKey = provider + ":" + (externalSourceId || "") + ":" + normalizedQuery.toLocaleLowerCase();
    const cached = suggestionCache.current.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) {
      setSuggestions(cached.values);
      setSuggestionsLoading(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSuggestionsLoading(true);
      try {
        const params = new URLSearchParams({ q: normalizedQuery, source_provider: provider, ...(externalSourceId ? { external_source_id: externalSourceId } : {}), limit: "10" });
        const response = await fetch("/api/v1/search/suggestions?" + params, { signal: controller.signal });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = payload?.detail;
          const code = detail && typeof detail === "object" ? detail.code : undefined;
          if (response.status === 409 && (code === "viewer_search_requires_v3" || code === "search_disabled")) {
            setCapabilities(current => ({ ...current, selected_version: "v1", v2_available: false }));
          }
          throw Error(searchApiErrorMessage(payload, "Suggestions are unavailable"));
        }
        const values = Array.isArray(payload.suggestions) ? payload.suggestions : [];
        suggestionCache.current.set(cacheKey, { values, expiresAt: Date.now() + SUGGESTION_CACHE_TTL_MS });
        setSuggestions(values);
      } catch {
        if (!controller.signal.aborted) setSuggestions([]);
      } finally {
        if (!controller.signal.aborted) setSuggestionsLoading(false);
      }
    }, SUGGESTION_DEBOUNCE_MS);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, authenticated, provider, query, externalSourceId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set("q", query.trim()); else params.delete("q");
    [...params.keys()].filter(key => key.startsWith("facet.")).forEach(key => params.delete(key));
    Object.entries(selectedFacets).forEach(([name, values]) => values.length && params.set("facet." + name, values.join(",")));
    const next = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (next ? "?" + next : ""));
  }, [query, facetKey]);

  const fetchPage = useCallback(async (
    cursor: string | null,
    append: boolean,
    epoch: number,
    signal: AbortSignal,
  ) => {
    const startedAt = performance.now();
    try {
      const response = await fetch("/api/v1/search", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal,
        body: JSON.stringify(buildSearchRequestBody(
          query, provider, selectedFacets, cursor, append, capabilities.debug_allowed, externalSourceId,
        )),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload?.detail;
        const code = detail && typeof detail === "object" ? detail.code : undefined;
        if (response.status === 409 && (code === "viewer_search_requires_v3" || code === "search_disabled")) {
          setCapabilities(current => ({ ...current, selected_version: "v1", v2_available: false }));
          setItems([]); setTotal(0); setError("");
          return;
        }
        throw Error(searchApiErrorMessage(payload, "Search is unavailable"));
      }
      if (!isCurrentSearchResponse(epoch, searchEpoch.current)) return;
      const pageItems: Asset[] = Array.isArray(payload.items) ? payload.items : [];
      const resultTotal = Number(payload.total || 0);
      setItems(current => append ? mergeSearchResults(current, pageItems) : pageItems);
      setTotal(resultTotal);
      if (!append) {
        setFacets(payload.facets || {});
        setParsed(payload.parsed_query || null);
        setDurationMs(Math.max(0, Math.round(performance.now() - startedAt)));
      }
      nextCursor.current = typeof payload.next_cursor === "string" && payload.next_cursor
        ? payload.next_cursor
        : null;
      setHasMore(nextCursor.current !== null);
    } catch (reason) {
      if (!signal.aborted && isCurrentSearchResponse(epoch, searchEpoch.current)) {
        setError(reason instanceof Error ? reason.message : "Search failed");
      }
    } finally {
      if (isCurrentSearchResponse(epoch, searchEpoch.current)) {
        if (append) setLoadingMore(false); else setLoading(false);
      }
      if (append) pageInFlight.current = false;
    }
  }, [capabilities.debug_allowed, provider, query, selectedFacets, externalSourceId]);

  useEffect(() => {
    const epoch = ++searchEpoch.current;
    appendController.current?.abort();
    appendController.current = null;
    pageInFlight.current = false;
    nextCursor.current = null;
    setHasMore(false);
    setLoadingMore(false);
    setItems([]);
    if (!active || !authenticated || query.trim().length < 1) {
      setTotal(0); setParsed(null); setDurationMs(null); setError(""); setLoading(false); return;
    }
    setDurationMs(null);
    setLoading(true);
    setError("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetchPage(null, false, epoch, controller.signal);
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, authenticated, query, facetKey, fetchPage]);

  const loadMore = useCallback(() => {
    if (!active || !authenticated || !query.trim() || !hasMore || loading || loadingMore || pageInFlight.current) return;
    pageInFlight.current = true;
    setLoadingMore(true);
    const controller = new AbortController();
    appendController.current?.abort();
    appendController.current = controller;
    void fetchPage(nextCursor.current, true, searchEpoch.current, controller.signal);
  }, [active, authenticated, query, hasMore, loading, loadingMore, fetchPage]);

  useEffect(() => () => appendController.current?.abort(), []);

  function clearSearchFilters() {
    setSelectedFacets({});
    setSuggestions([]);
    setFacets({});
    setParsed(null);
    setTotal(0);
    setDurationMs(null);
  }

  function toggleFacet(name: string, value: string) {
    setSelectedFacets(current => {
      const values = new Set(current[name] || []);
      values.has(value) ? values.delete(value) : values.add(value);
      return { ...current, [name]: [...values].sort() };
    });
  }

  return useMemo(() => ({
    active, capabilitiesResolved, capabilities, items, facets, selectedFacets, parsed,
    total, loading, loadingMore, hasMore, loadMore, durationMs, suggestions,
    suggestionsLoading, error, toggleFacet, clearSearchFilters,
  }), [active, capabilitiesResolved, capabilities, items, facets, selectedFacets, parsed, total, loading, loadingMore, hasMore, loadMore, durationMs, suggestions, suggestionsLoading, error]);
}
