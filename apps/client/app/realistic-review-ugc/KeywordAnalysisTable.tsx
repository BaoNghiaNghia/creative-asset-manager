import { useState } from "react";
import type { KeywordAnalysisSortBy, KeywordAnalysisSortDirection, KeywordTailFilter, KeywordUsageFilter } from "./api";
import type { KeywordVolumePage } from "./types";

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

function formatCpc(value: number | null): string {
  return typeof value === "number" ? "$" + value.toFixed(2) : "—";
}

function competitionTone(value: string | null): string {
  return (value || "unknown").toLowerCase();
}

function SortHeader({
  column,
  label,
  sortBy,
  sortDirection,
  onSortChange,
}: {
  column: KeywordAnalysisSortBy;
  label: string;
  sortBy: KeywordAnalysisSortBy;
  sortDirection: KeywordAnalysisSortDirection;
  onSortChange: (column: KeywordAnalysisSortBy) => void;
}) {
  const active = sortBy === column;
  return (
    <th aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        className={"rrugc-stage0-sort" + (active ? " active" : "")}
        onClick={() => onSortChange(column)}
        aria-label={
          "Sort by " + label + " "
          + (active && sortDirection === "asc" ? "descending" : "ascending")
        }
      >
        <span>{label}</span>
        <span className="rrugc-stage0-sort-icon" aria-hidden="true">
          {active ? (sortDirection === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
    </th>
  );
}

export function KeywordAnalysisTable({
  data,
  query,
  loading = false,
  sortBy,
  sortDirection,
  usageFilter,
  tailFilter,
  favoritesOnly,
  pickingIds,
  favoritingIds,
  onSortChange,
  onUsageFilterChange,
  onTailFilterChange,
  onFavoritesOnlyChange,
  onPickChange,
  onFavoriteChange,
  onPageChange,
  onPageSizeChange,
  onQueryChange,
}: {
  data: KeywordVolumePage;
  query: string;
  loading?: boolean;
  sortBy: KeywordAnalysisSortBy;
  sortDirection: KeywordAnalysisSortDirection;
  usageFilter: KeywordUsageFilter;
  tailFilter: KeywordTailFilter;
  favoritesOnly: boolean;
  pickingIds: Set<string>;
  favoritingIds: Set<string>;
  onSortChange: (column: KeywordAnalysisSortBy) => void;
  onUsageFilterChange: (filter: KeywordUsageFilter) => void;
  onTailFilterChange: (filter: KeywordTailFilter) => void;
  onFavoritesOnlyChange: (value: boolean) => void;
  onPickChange: (keywordId: string, picked: boolean) => void;
  onFavoriteChange: (keywordId: string, favorite: boolean) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onQueryChange: (query: string) => void;
}) {
  const [filter, setFilter] = useState<"all" | "high" | "zero">("all");
  const pageCount = Math.max(1, Math.ceil(data.total / Math.max(1, data.page_size)));
  const start = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const end = data.total === 0 ? 0 : Math.min(data.page * data.page_size, data.total);
  const items = data.items.filter(item => {
    if (filter === "high") return item.competition === "HIGH";
    if (filter === "zero") return item.search_volume <= 0;
    return true;
  });

  return <section className="rrugc-card rrugc-stage0">
    <div className="rrugc-section-heading rrugc-stage0-heading">
      <div>
        <small>QUOTE SCOUT → KEYWORD → GOOGLE ADS VOLUME</small>
        <h2>Stage 0 · Analysis Keyword</h2>
        <p>A separate quote-scout terminal discovers hat quote keywords, sends them to Creative Asset Manager, and CAM resolves Google Ads volume through AEBrowse.</p>
      </div>
      <div className="rrugc-stage0-heading-meta">
        <span className="rrugc-stage0-lane"><i aria-hidden="true" />Quote Scout · separate terminal</span>
        <small>Independent from the Stage 1 Pinterest reference scout.</small>
      </div>
    </div>

    <div className="rrugc-stage0-flow" aria-label="Keyword analysis workflow">
      <div><b>01</b><span><strong>Discover keyword</strong><small>Quote scout terminal</small></span></div>
      <i aria-hidden="true">→</i>
      <div><b>02</b><span><strong>Submit batch</strong><small>Up to 50 unique keywords</small></span></div>
      <i aria-hidden="true">→</i>
      <div><b>03</b><span><strong>Check volume</strong><small>AEBrowse · Google Ads</small></span></div>
      <i aria-hidden="true">→</i>
      <div><b>04</b><span><strong>Scout Pinterest</strong><small>Prioritize useful quotes</small></span></div>
    </div>

    <div className="rrugc-source-plan-kpis rrugc-stage0-kpis">
      <article><span>Total keywords</span><strong>{data.overview.total_keywords}</strong><small>Stored in Stage 0</small></article>
      <article><span>Monthly volume</span><strong>{data.overview.total_search_volume.toLocaleString()}</strong><small>Combined search volume</small></article>
      <article><span>High competition</span><strong>{data.overview.high_competition}</strong><small>Google Ads HIGH</small></article>
      <article><span>Zero volume</span><strong>{data.overview.zero_volume}</strong><small>Can deprioritize</small></article>
      <article className="rrugc-stage0-used-kpi"><span>Used</span><strong>{data.overview.picked_keywords}</strong><small>Picked for use</small></article>
      <article className="rrugc-stage0-favorite-kpi"><span>Favorites</span><strong>{data.overview.favorite_keywords}</strong><small>Saved for later</small></article>
    </div>

    <div className="rrugc-stage0-toolbar">
      <label className="rrugc-source-plan-search">
        <span className="sr-only">Search Stage 0 keywords</span>
        <input type="search" value={query} placeholder="Search keyword…" onChange={event => onQueryChange(event.target.value)} />
      </label>
      <div className="rrugc-stage0-filter-groups">
        <div className="rrugc-stage0-usage-filter" role="group" aria-label="Filter keyword usage">
          <button type="button" className={usageFilter === "all" ? "active" : ""} onClick={() => onUsageFilterChange("all")}>All <b>{data.overview.total_keywords}</b></button>
          <button type="button" className={usageFilter === "unused" ? "active" : ""} onClick={() => onUsageFilterChange("unused")}>Unused <b>{Math.max(0, data.overview.total_keywords - data.overview.picked_keywords)}</b></button>
          <button type="button" className={usageFilter === "used" ? "active" : ""} onClick={() => onUsageFilterChange("used")}>Used <b>{data.overview.picked_keywords}</b></button>
        </div>
        <button
          type="button"
          className={"rrugc-stage0-favorite-filter" + (favoritesOnly ? " active" : "")}
          aria-pressed={favoritesOnly}
          onClick={() => onFavoritesOnlyChange(!favoritesOnly)}
        ><span aria-hidden="true">★</span> Favorites <b>{data.overview.favorite_keywords}</b></button>
        <div className="rrugc-stage0-tail-filter" role="group" aria-label="Filter keyword length">
          <button type="button" className={tailFilter === "all" ? "active" : ""} aria-pressed={tailFilter === "all"} onClick={() => onTailFilterChange("all")}>All lengths</button>
          <button type="button" className={tailFilter === "short" ? "active" : ""} aria-pressed={tailFilter === "short"} title="2-word keywords" onClick={() => onTailFilterChange("short")}>Short-tail <small>2</small></button>
          <button type="button" className={tailFilter === "mid" ? "active" : ""} aria-pressed={tailFilter === "mid"} title="3–4 word keywords" onClick={() => onTailFilterChange("mid")}>Mid-tail <small>3–4</small></button>
          <button type="button" className={tailFilter === "long" ? "active" : ""} aria-pressed={tailFilter === "long"} title="5 or more words" onClick={() => onTailFilterChange("long")}>Long-tail <small>5+</small></button>
        </div>
        <div className="rrugc-stage0-filter" role="group" aria-label="Filter keyword volume">
          <button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>Any volume</button>
          <button type="button" className={filter === "high" ? "active" : ""} onClick={() => setFilter("high")}>High competition</button>
          <button type="button" className={filter === "zero" ? "active" : ""} onClick={() => setFilter("zero")}>Zero volume</button>
        </div>
      </div>
      <span className="rrugc-stage0-toolbar-count">{data.total} rows</span>
    </div>

    <div className="rrugc-stage0-table-wrap">
      <table className="rrugc-stage0-table rrugc-stage0-keyword-table" aria-busy={loading}>
        <thead><tr>
          <th>Image</th>
          <SortHeader column="keyword" label="Keyword" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="search_volume" label="Search volume" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="competition" label="Competition" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="cpc" label="CPC range" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="fetched_at" label="Last checked" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <th>Actions</th>
        </tr></thead>
        <tbody>
          {loading ? Array.from({length: 5}, (_, i) => <tr key={i} className="rrugc-stage0-skeleton-row"><td colSpan={7}><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-line" /></td></tr>) : items.map(item => (
            <tr key={item.id} className={item.picked ? "is-picked" : ""}>
              <td className="rrugc-stage0-source-image">
                {item.source_image_url ? (
                  <a href={item.source_pin_url || item.source_image_url} target="_blank" rel="noreferrer" title={"Open Pinterest source for " + item.keyword}>
                    <img src={item.source_image_url} alt="" loading="lazy" decoding="async" />
                  </a>
                ) : <span className="rrugc-stage0-source-empty">—</span>}
              </td>
              <td className="rrugc-stage0-keyword-name"><strong>{item.keyword}</strong><small>{item.provider === "aebrowse_google_ads" ? "AEBrowse · Google Ads" : item.provider}</small></td>
              <td className="rrugc-stage0-volume"><strong>{item.search_volume.toLocaleString()}</strong><small>/ month</small></td>
              <td><span className={"rrugc-stage0-competition competition-" + competitionTone(item.competition)}>{item.competition || "—"}</span></td>
              <td className="rrugc-stage0-cpc">{formatCpc(item.cpc_low)}–{formatCpc(item.cpc_high)}</td>
              <td className="rrugc-stage0-fetched"><strong>{new Date(item.fetched_at).toLocaleDateString()}</strong><small>{new Date(item.fetched_at).toLocaleTimeString()}</small></td>
              <td className="rrugc-stage0-pick-cell">
                <div className="rrugc-stage0-row-actions">
                <button
                  type="button"
                  className={"rrugc-stage0-pick" + (item.picked ? " is-picked" : "")}
                  aria-pressed={item.picked}
                  disabled={pickingIds.has(item.id)}
                  onClick={() => onPickChange(item.id, !item.picked)}
                  title={item.picked ? "Mark this keyword as unused" : "Mark this keyword as used"}
                >
                  <span aria-hidden="true">{item.picked ? "✓" : "+"}</span>
                  {pickingIds.has(item.id) ? "Saving…" : item.picked ? "Used" : "Pick"}
                </button>
                <button
                  type="button"
                  className={"rrugc-stage0-favorite" + (item.favorite ? " is-favorite" : "")}
                  aria-pressed={item.favorite}
                  aria-label={(item.favorite ? "Remove favorite: " : "Add favorite: ") + item.keyword}
                  title={item.favorite ? "Remove from favorites" : "Add to favorites"}
                  disabled={favoritingIds.has(item.id)}
                  onClick={() => onFavoriteChange(item.id, !item.favorite)}
                >
                  <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill={item.favorite ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m12 2.5 2.9 5.9 6.5.94-4.7 4.58 1.11 6.48L12 17.34l-5.81 3.06 1.11-6.48-4.7-4.58 6.5-.94z" /></svg>
                  {favoritingIds.has(item.id) ? "Saving…" : item.favorite ? "Saved" : "Favorite"}
                </button>
                </div>
                {item.picked && item.picked_at && <small>Used {new Date(item.picked_at).toLocaleDateString()}</small>}
              </td>
            </tr>
          ))}
          {!loading && items.length === 0 && <tr><td colSpan={7} className="rrugc-source-plan-empty">{query.trim() || tailFilter !== "all" || favoritesOnly ? "No keywords match these filters." : usageFilter !== "all" ? "No keywords in this usage state." : "No keyword data yet. Start the separate quote-scout terminal and submit discovered keywords."}</td></tr>}
        </tbody>
      </table>
    </div>

    <div className="rrugc-source-pagination rrugc-stage0-pagination">
      <span>{start}–{end} of {data.total}</span>
      <div className="rrugc-source-page-controls">
        <button type="button" disabled={loading || data.page <= 1} onClick={() => onPageChange(1)} aria-label="Stage 0 first page">«</button>
        <button type="button" disabled={loading || data.page <= 1} onClick={() => onPageChange(Math.max(1, data.page - 1))} aria-label="Stage 0 previous page">‹</button>
        <strong>Page {data.page} / {pageCount}</strong>
        <button type="button" disabled={loading || data.page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, data.page + 1))} aria-label="Stage 0 next page">›</button>
        <button type="button" disabled={loading || data.page >= pageCount} onClick={() => onPageChange(pageCount)} aria-label="Stage 0 last page">»</button>
      </div>
      <label>Rows<select aria-label="Stage 0 rows per page" value={data.page_size} disabled={loading} onChange={event => onPageSizeChange(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    </div>
  </section>;
}
