import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { fetchAccessIdentity } from "../../features/access_management";

export type RrugcSearchSuggestion = {
  value: string;
  meta?: string;
  badge?: string;
};

type SearchEntry = RrugcSearchSuggestion & {
  source: "history" | "completion" | "recommendation" | "direct";
};

const HISTORY_LIMIT = 10;
const HISTORY_PREFIX = "creative-asset-manager:rrugc-stage-search-history:v1:";

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, " ").slice(0, 200);
}

function historyKey(stageId: string, tenantId: string | null | undefined, userId: string | null | undefined): string | null {
  if (!tenantId || !userId) return null;
  return HISTORY_PREFIX + encodeURIComponent(stageId) + ":" + encodeURIComponent(tenantId) + ":" + encodeURIComponent(userId);
}

function loadHistory(key: string | null): string[] {
  if (!key || typeof window === "undefined") return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(key) || "[]");
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<string>();
    return parsed.flatMap(value => {
      const term = typeof value === "string" ? normalize(value) : "";
      const lower = term.toLocaleLowerCase();
      if (term.length < 2 || seen.has(lower)) return [];
      seen.add(lower);
      return [term];
    }).slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function saveHistory(key: string | null, values: string[]) {
  if (!key || typeof window === "undefined") return;
  try {
    if (values.length) window.localStorage.setItem(key, JSON.stringify(values.slice(0, HISTORY_LIMIT)));
    else window.localStorage.removeItem(key);
  } catch {
    // Search history is a convenience; privacy/quota failures must not block search.
  }
}

function appendHistory(entries: string[], value: string): string[] {
  const term = normalize(value);
  if (term.length < 2) return entries;
  return [term, ...entries.filter(item => item.toLocaleLowerCase() !== term.toLocaleLowerCase())]
    .slice(0, HISTORY_LIMIT);
}

function highlighted(value: string, query: string): ReactNode {
  const clean = query.trim();
  const offset = value.toLocaleLowerCase().indexOf(clean.toLocaleLowerCase());
  if (!clean || offset < 0) return value;
  return <>{value.slice(0, offset)}<mark>{value.slice(offset, offset + clean.length)}</mark>{value.slice(offset + clean.length)}</>;
}

function sectionLabel(source: SearchEntry["source"]): string {
  if (source === "history") return "Recent searches";
  if (source === "completion") return "Search completion";
  if (source === "recommendation") return "Recommended";
  return "Search this term";
}

function sourceIcon(source: SearchEntry["source"]): string {
  if (source === "history") return "↺";
  if (source === "completion") return "↳";
  if (source === "recommendation") return "⌕";
  return "→";
}

export function RrugcSmartSearchInput({
  stageId,
  query,
  onQueryChange,
  suggestions = [],
  placeholder,
  label,
  className = "",
}: {
  stageId: string;
  query: string;
  onQueryChange: (next: string) => void;
  suggestions?: RrugcSearchSuggestion[];
  placeholder: string;
  label: string;
  className?: string;
}) {
  const [storageKey, setStorageKey] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const clean = normalize(query);
  const inputId = "rrugc-" + stageId + "-search-input";
  const listboxId = "rrugc-" + stageId + "-search-options";

  useEffect(() => {
    let mounted = true;
    void fetchAccessIdentity()
      .then(identity => {
        if (mounted) setStorageKey(historyKey(stageId, identity.active_tenant_id, identity.user_id));
      })
      .catch(() => {
        if (mounted) setStorageKey(null);
      });
    return () => { mounted = false; };
  }, [stageId]);

  useEffect(() => {
    setHistory(loadHistory(storageKey));
  }, [storageKey]);

  const entries = useMemo(() => {
    const lower = clean.toLocaleLowerCase();
    const historyMatches = history
      .filter(value => !clean || value.toLocaleLowerCase().includes(lower))
      .slice(0, clean ? 4 : 8);
    const seen = new Set(historyMatches.map(value => value.toLocaleLowerCase()));
    const result: SearchEntry[] = historyMatches.map(value => ({ value, source: "history" }));

    if (clean.length >= 2) {
      const uniqueSuggestions: RrugcSearchSuggestion[] = [];
      for (const suggestion of suggestions) {
        const value = normalize(suggestion.value);
        if (value.length < 2) continue;
        const key = value.toLocaleLowerCase();
        if (seen.has(key) || uniqueSuggestions.some(item => item.value.toLocaleLowerCase() === key)) continue;
        uniqueSuggestions.push({ ...suggestion, value });
      }

      const completions = uniqueSuggestions
        .filter(item => item.value.toLocaleLowerCase().startsWith(lower))
        .slice(0, 4);
      for (const item of completions) {
        seen.add(item.value.toLocaleLowerCase());
        result.push({ ...item, source: "completion" });
      }

      const recommendations = uniqueSuggestions
        .filter(item => !seen.has(item.value.toLocaleLowerCase()))
        .slice(0, 5);
      for (const item of recommendations) {
        seen.add(item.value.toLocaleLowerCase());
        result.push({ ...item, source: "recommendation" });
      }

      if (!seen.has(lower)) {
        result.push({ value: clean, source: "direct", meta: "Use exactly this search" });
      }
    }
    return result;
  }, [clean, history, suggestions]);

  function remember(value: string) {
    if (!storageKey) return;
    setHistory(current => {
      const next = appendHistory(current, value);
      saveHistory(storageKey, next);
      return next;
    });
  }

  function select(value: string) {
    const next = normalize(value);
    onQueryChange(next);
    remember(next);
    setExpanded(false);
    setActiveIndex(-1);
    inputRef.current?.focus();
  }

  function removeHistory(value: string) {
    setHistory(current => {
      const next = current.filter(item => item.toLocaleLowerCase() !== value.toLocaleLowerCase());
      saveHistory(storageKey, next);
      return next;
    });
    setActiveIndex(-1);
  }

  function clearHistory() {
    setHistory([]);
    saveHistory(storageKey, []);
    setActiveIndex(-1);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setExpanded(false);
      setActiveIndex(-1);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!entries.length) return;
      event.preventDefault();
      setExpanded(true);
      setActiveIndex(current => event.key === "ArrowDown"
        ? (current + 1) % entries.length
        : (current - 1 + entries.length) % entries.length);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (expanded && activeIndex >= 0 && entries[activeIndex]) select(entries[activeIndex].value);
      else {
        remember(clean);
        setExpanded(false);
      }
    }
  }

  const showPanel = expanded && entries.length > 0;
  let previousSource: SearchEntry["source"] | null = null;

  return <div className={"rrugc-smart-search rrugc-stage0-search" + (className ? " " + className : "")}>
    <div className="rrugc-stage0-search-field">
      <label htmlFor={inputId} className="sr-only">{label}</label>
      <span className="rrugc-stage0-search-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></svg>
      </span>
      <input
        id={inputId}
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-controls={showPanel ? listboxId : undefined}
        aria-expanded={showPanel}
        aria-activedescendant={showPanel && activeIndex >= 0 ? listboxId + "-" + activeIndex : undefined}
        autoComplete="off"
        maxLength={200}
        value={query}
        placeholder={placeholder}
        onChange={event => {
          onQueryChange(event.target.value);
          setExpanded(true);
          setActiveIndex(-1);
        }}
        onFocus={() => setExpanded(true)}
        onClick={() => setExpanded(true)}
        onBlur={() => {
          remember(clean);
          setExpanded(false);
          setActiveIndex(-1);
        }}
        onKeyDown={onKeyDown}
      />
    </div>

    {showPanel && <div className="rrugc-stage0-suggest-panel rrugc-smart-search-panel">
      <ul id={listboxId} role="listbox" aria-label={label + " recommendations, completion, and history"}>
        {entries.map((entry, index) => {
          const addLabel = previousSource !== entry.source;
          previousSource = entry.source;
          return <Fragment key={entry.source + ":" + entry.value}>
            {addLabel && <li role="presentation" className="rrugc-stage0-suggest-label rrugc-smart-search-label">
              <span>{sectionLabel(entry.source)}</span>
              {entry.source === "history" && <button
                type="button"
                onMouseDown={event => event.preventDefault()}
                onClick={clearHistory}
              >Clear history</button>}
            </li>}
            <li role="presentation" className={activeIndex === index ? "is-active" : ""}>
              <button
                id={listboxId + "-" + index}
                type="button"
                role="option"
                aria-selected={activeIndex === index}
                className="rrugc-stage0-suggest-item"
                onMouseDown={event => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => select(entry.value)}
              >
                <span className="rrugc-stage0-suggest-icon" aria-hidden="true">{sourceIcon(entry.source)}</span>
                <span className="rrugc-stage0-suggest-text">{highlighted(entry.value, query)}</span>
                {entry.badge && <span className="rrugc-smart-search-badge">{entry.badge}</span>}
                {entry.meta && <small>{entry.meta}</small>}
              </button>
              {entry.source === "history" && <button
                type="button"
                className="rrugc-stage0-history-remove"
                aria-label={"Remove from search history: " + entry.value}
                title="Remove from history"
                onMouseDown={event => event.preventDefault()}
                onClick={() => removeHistory(entry.value)}
              >×</button>}
            </li>
          </Fragment>;
        })}
      </ul>
    </div>}
  </div>;
}
