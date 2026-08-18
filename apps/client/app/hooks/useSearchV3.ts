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
  selected_version: "v3", readiness: "unavailable", search_available: false,
  viewer_scoped: false, failure_code: "search_v3_unavailable", facet_names: [], examples: [],
};
const SUGGESTION_DEBOUNCE_MS = 60;
const SUGGESTION_CACHE_TTL_MS = 20_000;
export const SEARCH_PAGE_SIZE = 60;

export function isSearchV3Active(capabilitiesResolved: boolean, capabilities: SearchCapabilities): boolean {
  return capabilitiesResolved && capabilities.selected_version === "v3" && capabilities.search_available;
}

export function shouldFetchSearchSuggestions(active: boolean, authenticated: boolean, query: string): boolean {
  return active && authenticated && query.trim().length >= 2;
}

export function suggestionQueriesMatch(previousQuery: string, currentQuery: string): boolean {
  const previous = previousQuery.trim().toLocaleLowerCase();
  const current = currentQuery.trim().toLocaleLowerCase();
  return Boolean(previous && current) && (previous.startsWith(current) || current.startsWith(previous));
}

export function isSuggestionAuthorizationDenial(status: number): boolean {
  return status === 401 || status === 403;
}

export function shouldPreserveSuggestionsAfterFailure(status: number, currentQueryMatches: boolean): boolean {
  return currentQueryMatches && !isSuggestionAuthorizationDenial(status);
}

export function isCurrentSuggestionResponse(
  requestEpoch: number, currentEpoch: number, requestScope: string, currentScope: string,
): boolean {
  return requestEpoch === currentEpoch && requestScope === currentScope;
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

export function useSearchV3(authenticated: boolean, provider: Provider, query: string, externalSourceId?: string | null, paginationResetKey?: string | null) {
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
  const [suggestionsError, setSuggestionsError] = useState("");
  const [suggestionsLastUpdated, setSuggestionsLastUpdated] = useState<number | null>(null);
  const [suggestionsRetry, setSuggestionsRetry] = useState(0);
  const [error, setError] = useState("");
  const [capabilitiesError, setCapabilitiesError] = useState("");
  const [capabilitiesRetry, setCapabilitiesRetry] = useState(0);
  const [capabilitiesResolved, setCapabilitiesResolved] = useState(false);
  const suggestionCache = useRef(new Map<string, { expiresAt: number; updatedAt: number; values: SearchSuggestion[] }>());
  const suggestionEpoch = useRef(0);
  const suggestionController = useRef<AbortController | null>(null);
  const suggestionRequestScope = useRef("");
  const lastSuccessfulSuggestions = useRef<{ scope: string; query: string } | null>(null);
  const searchEpoch = useRef(0);
  const nextCursor = useRef<string | null>(null);
  const pageInFlight = useRef(false);
  const appendController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!authenticated) {
      suggestionController.current?.abort();
      suggestionController.current = null;
      suggestionEpoch.current += 1;
      suggestionRequestScope.current = "";
      lastSuccessfulSuggestions.current = null;
      suggestionCache.current.clear();
      setSuggestions([]); setSuggestionsLoading(false); setSuggestionsError("");
      setSuggestionsLastUpdated(null);
      setCapabilities(emptyCapabilities);
      setCapabilitiesResolved(false);
      return;
    }
    setCapabilitiesResolved(false);
    setCapabilities(emptyCapabilities);
    setCapabilitiesError("");
    const controller = new AbortController();
    fetch("/api/v1/search/capabilities", { signal: controller.signal })
      .then(async response => {
        if (!response.ok) { const payload = await response.json().catch(() => null); throw Error(searchApiErrorMessage(payload, "Search is unavailable")); }
        setCapabilities(await response.json());
      })
      .catch(reason => !controller.signal.aborted && setCapabilitiesError(reason.message))
      .finally(() => !controller.signal.aborted && setCapabilitiesResolved(true));
    return () => controller.abort();
  }, [authenticated, provider, capabilitiesRetry]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial: Record<string, string[]> = {};
    params.forEach((value, key) => {
      if (key.startsWith("facet.")) initial[key.slice(6)] = value.split(",").filter(Boolean);
    });
    setSelectedFacets(initial);
  }, []);

  const active = isSearchV3Active(capabilitiesResolved, capabilities);
  const facetKey = JSON.stringify(selectedFacets);
  const viewerSourceMissing = Boolean(capabilities.viewer_scoped) && !externalSourceId?.trim();

  useEffect(() => {
    const epoch = ++suggestionEpoch.current;
    suggestionController.current?.abort();
    suggestionController.current = null;
    const normalizedQuery = query.trim();
    const sourceId = externalSourceId?.trim() || "";
    const scope = provider + ":" + sourceId;
    const requestScope = scope + ":" + normalizedQuery.toLocaleLowerCase();
    suggestionRequestScope.current = requestScope;
    const previous = lastSuccessfulSuggestions.current;
    const canPreserve = Boolean(
      previous && previous.scope === scope
      && suggestionQueriesMatch(previous.query, normalizedQuery),
    );
    const clearSuggestionState = () => {
      setSuggestions([]);
      setSuggestionsLastUpdated(null);
      lastSuccessfulSuggestions.current = null;
    };
    if (!authenticated || !normalizedQuery) {
      clearSuggestionState();
      setSuggestionsLoading(false); setSuggestionsError("");
      return;
    }
    if (previous && (previous.scope !== scope || previous.query !== normalizedQuery)) clearSuggestionState();
    if (viewerSourceMissing || !shouldFetchSearchSuggestions(active, authenticated, normalizedQuery)) {
      setSuggestionsLoading(false); setSuggestionsError("");
      return;
    }
    const cacheKey = scope + ":" + normalizedQuery.toLocaleLowerCase();
    const cached = suggestionCache.current.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) {
      setSuggestions(cached.values);
      setSuggestionsLastUpdated(cached.updatedAt);
      lastSuccessfulSuggestions.current = { scope, query: normalizedQuery };
      setSuggestionsLoading(false);
      setSuggestionsError("");
      return;
    }
    const controller = new AbortController();
    suggestionController.current = controller;
    const timer = window.setTimeout(async () => {
      setSuggestionsLoading(true);
      setSuggestionsError("");
      try {
        const params = new URLSearchParams({ q: normalizedQuery, source_provider: provider, ...(sourceId ? { external_source_id: sourceId } : {}), limit: "10" });
        const response = await fetch("/api/v1/search/suggestions?" + params, { signal: controller.signal });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw Object.assign(Error(searchApiErrorMessage(payload, "Suggestions are unavailable")), { status: response.status });
        }
        const values: SearchSuggestion[] = Array.isArray(payload.suggestions) ? payload.suggestions : [];
        if (controller.signal.aborted || !isCurrentSuggestionResponse(epoch, suggestionEpoch.current, requestScope, suggestionRequestScope.current)) return;
        const updatedAt = Date.now();
        suggestionCache.current.set(cacheKey, { values, updatedAt, expiresAt: updatedAt + SUGGESTION_CACHE_TTL_MS });
        lastSuccessfulSuggestions.current = { scope, query: normalizedQuery };
        setSuggestions(values);
        setSuggestionsLastUpdated(updatedAt);
        setSuggestionsError("");
      } catch (reason) {
        if (controller.signal.aborted || !isCurrentSuggestionResponse(epoch, suggestionEpoch.current, requestScope, suggestionRequestScope.current)) return;
        const failure = reason as Error & { status?: number };
        if (isSuggestionAuthorizationDenial(failure.status || 0)) {
          suggestionCache.current.clear();
        }
        if (!shouldPreserveSuggestionsAfterFailure(failure.status || 0, canPreserve)) {
          clearSuggestionState();
        }
        setSuggestionsError(failure.message || "Suggestions are unavailable");
      } finally {
        if (!controller.signal.aborted && isCurrentSuggestionResponse(epoch, suggestionEpoch.current, requestScope, suggestionRequestScope.current)) {
          setSuggestionsLoading(false);
        }
      }
    }, SUGGESTION_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (suggestionController.current === controller) suggestionController.current = null;
    };
  }, [active, authenticated, provider, query, externalSourceId, viewerSourceMissing, suggestionsRetry]);

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
    if (viewerSourceMissing) {
      if (append) setLoadingMore(false); else setLoading(false);
      setError("Search source is unavailable");
      return;
    }
    const startedAt = performance.now();
    try {
      const response = await fetch("/api/v1/search", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal,
        body: JSON.stringify(buildSearchRequestBody(
          query, provider, selectedFacets, cursor, append, false, externalSourceId,
        )),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw Error(searchApiErrorMessage(payload, "Search is unavailable"));
      }
      if (!isCurrentSearchResponse(epoch, searchEpoch.current)) return;
      const pageItems: Asset[] = Array.isArray(payload.items) ? payload.items : [];
      const resultTotal = Number(payload.total || 0);
      setItems(current => append ? mergeSearchResults(current, pageItems) : mergeSearchResults([], pageItems));
      setTotal(resultTotal);
      if (!append) {
        setFacets(payload.facets || {});
        setParsed(payload.parsed_query || null);
        setDurationMs(Math.max(0, Math.round(performance.now() - startedAt)));
      }
      nextCursor.current = typeof payload.next_cursor === "string" && payload.next_cursor
        ? payload.next_cursor
        : null;
      setHasMore(Boolean(payload.has_more && nextCursor.current));
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
  }, [provider, query, selectedFacets, externalSourceId, viewerSourceMissing]);

  useEffect(() => {
    const epoch = ++searchEpoch.current;
    appendController.current?.abort();
    appendController.current = null;
    pageInFlight.current = false;
    nextCursor.current = null;
    setHasMore(false);
    setLoadingMore(false);
    setItems([]);
    if (viewerSourceMissing || !active || !authenticated || query.trim().length < 1) {
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
  }, [active, authenticated, query, facetKey, fetchPage, viewerSourceMissing, paginationResetKey]);

  const loadMore = useCallback(() => {
    if (viewerSourceMissing || !active || !authenticated || !query.trim() || !hasMore || !nextCursor.current || loading || loadingMore || pageInFlight.current) return;
    pageInFlight.current = true;
    setLoadingMore(true);
    const controller = new AbortController();
    appendController.current?.abort();
    appendController.current = controller;
    void fetchPage(nextCursor.current, true, searchEpoch.current, controller.signal);
  }, [active, authenticated, query, hasMore, loading, loadingMore, fetchPage, viewerSourceMissing]);

  useEffect(() => () => appendController.current?.abort(), []);

  function clearSearchFilters() {
    setSelectedFacets({});
    setSuggestions([]);
    setSuggestionsError("");
    setSuggestionsLastUpdated(null);
    lastSuccessfulSuggestions.current = null;
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

  const availabilityError = capabilitiesResolved && !capabilities.search_available
    ? "Search V3 is unavailable. Retry when the search index is ready."
    : "";
  const displayedError = error || (query.trim() ? capabilitiesError || availabilityError : "");
  const retry = useCallback(() => setCapabilitiesRetry(current => current + 1), []);
  const retrySuggestions = useCallback(() => setSuggestionsRetry(current => current + 1), []);

  return useMemo(() => ({
    active, capabilitiesResolved, capabilities, items, facets, selectedFacets, parsed,
    total, loading, loadingMore, hasMore, loadMore, durationMs, suggestions,
    suggestionsLoading, suggestionsError, suggestionsLastUpdated, retrySuggestions,
    error: displayedError, retry, toggleFacet, clearSearchFilters,
  }), [active, capabilitiesResolved, capabilities, items, facets, selectedFacets, parsed, total, loading, loadingMore, hasMore, loadMore, durationMs, suggestions, suggestionsLoading, suggestionsError, suggestionsLastUpdated, displayedError, retry, retrySuggestions]);
}
