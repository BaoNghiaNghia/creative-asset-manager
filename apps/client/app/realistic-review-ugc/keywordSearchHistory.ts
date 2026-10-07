// Stage 0 history is a browser-only convenience. Keep it isolated by tenant
// AND user; never reuse the global Asset Explorer or public-share history key.
export const KEYWORD_SEARCH_HISTORY_LIMIT = 10;
const PREFIX = "creative-asset-manager:rrugc-stage0-keyword-history:v1:";

export function keywordHistoryKey(tenantId: string | null | undefined, userId: string | null | undefined): string | null {
  return tenantId && userId ? PREFIX + encodeURIComponent(tenantId) + ":" + encodeURIComponent(userId) : null;
}

function normalized(value: string): string {
  return value.trim().replace(/\s+/g, " ").slice(0, 200);
}

export function readKeywordHistory(value: string | null | undefined): string[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<string>();
    return parsed.flatMap(entry => {
      const term = typeof entry === "string" ? normalized(entry) : "";
      const key = term.toLocaleLowerCase();
      if (term.length < 2 || seen.has(key)) return [];
      seen.add(key);
      return [term];
    }).slice(0, KEYWORD_SEARCH_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

export function appendKeywordHistory(entries: string[], value: string): string[] {
  const term = normalized(value);
  if (term.length < 2) return entries;
  return [term, ...entries.filter(item => item.toLocaleLowerCase() !== term.toLocaleLowerCase())]
    .slice(0, KEYWORD_SEARCH_HISTORY_LIMIT);
}

export function loadKeywordHistory(key: string | null): string[] {
  if (!key || typeof window === "undefined") return [];
  try {
    return readKeywordHistory(window.localStorage.getItem(key));
  } catch {
    return [];
  }
}

export function saveKeywordHistory(key: string | null, values: string[]): void {
  if (!key || typeof window === "undefined") return;
  try {
    if (values.length) {
      window.localStorage.setItem(key, JSON.stringify(values.slice(0, KEYWORD_SEARCH_HISTORY_LIMIT)));
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Quota errors / privacy mode must never block keyword search.
  }
}
