export const SEARCH_HISTORY_STORAGE_KEY = "creative-asset-manager:search-history:v1";
export const SEARCH_HISTORY_LIMIT = 10;

function storage(): Storage | null {
  return typeof window !== "undefined" ? window.localStorage : null;
}

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function readSearchHistory(value: string | null | undefined): string[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<string>();
    return parsed.flatMap(item => {
      const text = typeof item === "string" ? normalize(item) : "";
      const key = text.toLocaleLowerCase();
      if (!text || seen.has(key)) return [];
      seen.add(key);
      return [text];
    }).slice(0, SEARCH_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

export function loadSearchHistory(): string[] {
  try {
    return readSearchHistory(storage()?.getItem(SEARCH_HISTORY_STORAGE_KEY));
  } catch {
    return [];
  }
}

export function saveSearchHistory(entries: string[]): void {
  try {
    storage()?.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(entries.slice(0, SEARCH_HISTORY_LIMIT)));
  } catch {
    // Browser storage is an optional convenience and must never block search.
  }
}

export function addSearchHistory(entries: string[], query: string): string[] {
  const text = normalize(query);
  if (!text) return entries;
  const key = text.toLocaleLowerCase();
  return [text, ...entries.filter(item => item.toLocaleLowerCase() !== key)].slice(0, SEARCH_HISTORY_LIMIT);
}
