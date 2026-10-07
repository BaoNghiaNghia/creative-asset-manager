import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import { suggestKeywordAnalysis, type KeywordSearchSuggestion } from "./api";
import {
  appendKeywordHistory,
  keywordHistoryKey,
  loadKeywordHistory,
  saveKeywordHistory,
} from "./keywordSearchHistory";

type Entry = {
  keyword: string;
  source: "history" | "suggestion";
  searchVolume?: number;
  favorite?: boolean;
};

function highlighted(keyword: string, query: string): ReactNode {
  const offset = keyword.toLocaleLowerCase().indexOf(query.trim().toLocaleLowerCase());
  if (!query.trim() || offset < 0) return keyword;
  return <>{keyword.slice(0, offset)}<mark>{keyword.slice(offset, offset + query.trim().length)}</mark>{keyword.slice(offset + query.trim().length)}</>;
}

export function KeywordSearchInput({
  query,
  onQueryChange,
}: {
  query: string;
  onQueryChange: (next: string) => void;
}) {
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<KeywordSearchSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const clean = query.trim();

  useEffect(() => {
    let mounted = true;
    void fetchAccessIdentity().then(identity => {
      if (mounted) setHistoryKey(keywordHistoryKey(identity.active_tenant_id, identity.user_id));
    }).catch(() => {
      if (mounted) setHistoryKey(null);
    });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    // Reset immediately on identity/tenant changes to avoid cross-scope UI leaks.
    setHistory(loadKeywordHistory(historyKey));
  }, [historyKey]);

  useEffect(() => {
    setActiveIndex(-1);
    setSuggestions([]);
    if (clean.length < 2) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      void suggestKeywordAnalysis(clean.slice(0, 100), controller.signal)
        .then(items => {
          if (!controller.signal.aborted) setSuggestions(items);
        })
        .catch(() => {
          if (!controller.signal.aborted) setSuggestions([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [clean]);

  const entries = useMemo(() => {
    const matches = history
      .filter(value => !clean || value.toLocaleLowerCase().includes(clean.toLocaleLowerCase()))
      .slice(0, clean ? 4 : 8);
    const seen = new Set(matches.map(value => value.toLocaleLowerCase()));
    const result: Entry[] = matches.map(keyword => ({ keyword, source: "history" }));
    if (clean.length >= 2) {
      for (const entry of suggestions) {
        const key = entry.keyword.toLocaleLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        result.push({
          keyword: entry.keyword,
          source: "suggestion",
          searchVolume: entry.search_volume,
          favorite: entry.favorite,
        });
        if (result.length >= 10) break;
      }
    }
    return result;
  }, [clean, history, suggestions]);

  function remember(value: string) {
    if (!historyKey) return;
    setHistory(prev => {
      const next = appendKeywordHistory(prev, value);
      saveKeywordHistory(historyKey, next);
      return next;
    });
  }

  function select(value: string) {
    onQueryChange(value);
    remember(value);
    setExpanded(false);
    setActiveIndex(-1);
    inputRef.current?.focus();
  }

  function removeHistory(value: string) {
    setHistory(prev => {
      const next = prev.filter(item => item.toLocaleLowerCase() !== value.toLocaleLowerCase());
      saveKeywordHistory(historyKey, next);
      return next;
    });
    setActiveIndex(-1);
  }

  function clearHistory() {
    setHistory([]);
    saveKeywordHistory(historyKey, []);
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
      if (expanded && activeIndex >= 0 && entries[activeIndex]) {
        select(entries[activeIndex].keyword);
      } else {
        remember(clean);
        setExpanded(false);
      }
    }
  }

  const recentCount = entries.filter(entry => entry.source === "history").length;
  const showPanel = expanded && (entries.length > 0 || (loading && clean.length >= 2));
  const listboxId = "rrugc-stage0-search-options";

  return <div className="rrugc-source-plan-search rrugc-stage0-search">
    <div className="rrugc-stage0-search-field">
    <label htmlFor="rrugc-stage0-search-input" className="sr-only">Search Stage 0 keywords</label>
    <span className="rrugc-stage0-search-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></svg>
    </span>
    <input
      id="rrugc-stage0-search-input"
      ref={inputRef}
      type="search"
      role="combobox"
      aria-label="Search Stage 0 keywords"
      aria-autocomplete="list"
      aria-controls={showPanel ? listboxId : undefined}
      aria-expanded={showPanel}
      aria-activedescendant={showPanel && activeIndex >= 0 ? "rrugc-stage0-option-" + activeIndex : undefined}
      autoComplete="off"
      maxLength={200}
      value={query}
      placeholder="Search keyword…"
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
    {showPanel && <div className="rrugc-stage0-suggest-panel">
      {recentCount > 0 && <div className="rrugc-stage0-suggest-head">
        <span>Recent searches</span>
        <button type="button" onMouseDown={event => event.preventDefault()} onClick={clearHistory}>Clear history</button>
      </div>}
      <ul id={listboxId} role="listbox" aria-label="Keyword recommendations and history">
        {entries.map((entry, index) => <Fragment key={entry.source + ":" + entry.keyword}>
          {entry.source === "suggestion" && index === recentCount && <li role="presentation" className="rrugc-stage0-suggest-label">Keyword recommendations</li>}
          <li role="presentation" className={activeIndex === index ? "is-active" : ""}>
          <button
            id={"rrugc-stage0-option-" + index}
            type="button"
            role="option"
            aria-selected={activeIndex === index}
            className="rrugc-stage0-suggest-item"
            onMouseDown={event => event.preventDefault()}
            onMouseEnter={() => setActiveIndex(index)}
            onClick={() => select(entry.keyword)}
          >
            <span className="rrugc-stage0-suggest-icon" aria-hidden="true">{entry.source === "history" ? "↺" : "⌕"}</span>
            <span className="rrugc-stage0-suggest-text">{highlighted(entry.keyword, query)}</span>
            {entry.favorite && <span className="rrugc-stage0-suggest-star" aria-label="Favorite">★</span>}
            {typeof entry.searchVolume === "number" && <small>{entry.searchVolume.toLocaleString()}/mo</small>}
          </button>
          {entry.source === "history" && <button
            type="button"
            className="rrugc-stage0-history-remove"
            aria-label={"Remove from search history: " + entry.keyword}
            title="Remove from history"
            onMouseDown={event => event.preventDefault()}
            onClick={() => removeHistory(entry.keyword)}
          >×</button>}
          </li>
        </Fragment>)}
        {loading && <li className="rrugc-stage0-suggest-loading" role="presentation">Finding similar keywords…</li>}
      </ul>
    </div>}
  </div>;
}
