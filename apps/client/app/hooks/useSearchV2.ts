import { useEffect, useMemo, useState } from "react";
import type { Asset, ParsedQueryDebug, Provider, SearchCapabilities, SearchFacetBucket } from "../types";

const emptyCapabilities: SearchCapabilities = {
  selected_version: "v1", v2_available: false, parser_available: false,
  debug_allowed: false, facet_names: [], examples: [],
};

export function useSearchV2(authenticated: boolean, provider: Provider, query: string) {
  const [capabilities, setCapabilities] = useState(emptyCapabilities);
  const [items, setItems] = useState<Asset[]>([]);
  const [facets, setFacets] = useState<Record<string, SearchFacetBucket[]>>({});
  const [selectedFacets, setSelectedFacets] = useState<Record<string, string[]>>({});
  const [parsed, setParsed] = useState<ParsedQueryDebug | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [capabilitiesResolved, setCapabilitiesResolved] = useState(false);

  useEffect(() => {
    if (!authenticated) { setCapabilities(emptyCapabilities); setCapabilitiesResolved(false); return; }
    setCapabilitiesResolved(false);
    const controller = new AbortController();
    fetch("/api/v1/search/capabilities", { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw Error("Unable to read search capabilities");
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
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set("q", query.trim()); else params.delete("q");
    [...params.keys()].filter(key => key.startsWith("facet.")).forEach(key => params.delete(key));
    Object.entries(selectedFacets).forEach(([name, values]) => values.length && params.set("facet." + name, values.join(",")));
    const next = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (next ? "?" + next : ""));
  }, [query, facetKey]);

  useEffect(() => {
    if (!active || !authenticated || query.trim().length < 1) {
      setItems([]); setTotal(0); setParsed(null); setError(""); return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true); setError("");
      try {
        const response = await fetch("/api/v1/search", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
          body: JSON.stringify({ query: query.trim(), source_provider: provider, facets: selectedFacets, limit: 200, debug: capabilities.debug_allowed }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw Error(payload.detail || "Search is unavailable");
        setItems(payload.items || []); setTotal(payload.total || 0);
        setFacets(payload.facets || {}); setParsed(payload.parsed_query || null);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Search failed");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, authenticated, provider, query, facetKey, capabilities.debug_allowed]);

  function toggleFacet(name: string, value: string) {
    setSelectedFacets(current => {
      const values = new Set(current[name] || []);
      values.has(value) ? values.delete(value) : values.add(value);
      return { ...current, [name]: [...values].sort() };
    });
  }

  return useMemo(() => ({ active, capabilitiesResolved, capabilities, items, facets, selectedFacets, parsed, total, loading, error, toggleFacet }), [active, capabilitiesResolved, capabilities, items, facets, selectedFacets, parsed, total, loading, error]);
}
