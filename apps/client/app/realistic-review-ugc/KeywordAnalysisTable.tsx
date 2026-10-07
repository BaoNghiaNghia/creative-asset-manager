import { KeywordSearchInput } from "./KeywordSearchInput";
import type { KeywordAnalysisSortBy, KeywordAnalysisSortDirection, KeywordTailFilter, KeywordUsageFilter } from "./api";
import type { KeywordVolumePage } from "./types";

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

type KeywordTailKind = Exclude<KeywordTailFilter, "all">;

function KeywordTailIcon({ kind, className = "" }: { kind: KeywordTailKind; className?: string }) {
  const tokens = kind === "short"
    ? [[2, 8, 8, 7], [12, 8, 13, 7]]
    : kind === "mid"
      ? [[2, 4, 8, 6], [12, 4, 13, 6], [2, 14, 11, 6], [15, 14, 9, 6]]
      : [[2, 2, 8, 5], [12, 2, 13, 5], [2, 9.5, 11, 5], [15, 9.5, 9, 5], [2, 17, 6, 5], [10, 17, 14, 5]];
  return <svg className={className} viewBox="0 0 27 24" aria-hidden="true" focusable="false">
    {tokens.map(([x, y, width, height], index) => (
      <rect key={index} x={x} y={y} width={width} height={height} rx={height / 2} fill="currentColor" opacity={1 - index * .08} />
    ))}
  </svg>;
}

function Stage0ActionIcon({ kind }: { kind: "all" | "unused" | "used" | "favorite" | "pick" | "picked" }) {
  if (kind === "all") return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="6.5" height="6.5" rx="1.5" /><rect x="14" y="3.5" width="6.5" height="6.5" rx="1.5" /><rect x="3.5" y="14" width="6.5" height="6.5" rx="1.5" /><rect x="14" y="14" width="6.5" height="6.5" rx="1.5" /></svg>;
  if (kind === "unused") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7.5" /><path d="M8.5 12h7" /></svg>;
  if (kind === "used") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="m8.4 12.1 2.3 2.3 4.9-5.1" /></svg>;
  if (kind === "favorite") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3.1 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" /></svg>;
  if (kind === "picked") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="m8.4 12.1 2.3 2.3 4.9-5.1" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 8v8M8 12h8" /></svg>;
}

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
  onResetAll,
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
  onResetAll: () => void;
  onPickChange: (keywordId: string, picked: boolean) => void;
  onFavoriteChange: (keywordId: string, favorite: boolean) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onQueryChange: (query: string) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(data.total / Math.max(1, data.page_size)));
  const start = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const end = data.total === 0 ? 0 : Math.min(data.page * data.page_size, data.total);
  const items = data.items;

  return <section className="rrugc-card rrugc-stage0">
    <div className="rrugc-section-heading rrugc-stage0-heading">
      <div>
        <small>QUOTE SCOUT → KEYWORD → GOOGLE ADS VOLUME</small>
        <h2>Stage 0 · Analysis Keyword</h2>
        <p>A separate quote-scout terminal discovers hat quote keywords, sends them to Creative Asset Manager, and CAM resolves Google Ads volume through AEBrowse.</p>
      </div>
      <div className="rrugc-stage0-heading-meta">
        <span className="rrugc-stage0-lane"><i aria-hidden="true" />Quote Scout · separate terminal</span>
        <small>Independent from the Stage 2 Pinterest reference scout.</small>
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
      <article className="rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-short">
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="short" /></i><b>Short-tail</b></span>
        <strong>{data.overview.short_tail_keywords}</strong>
        <small>2-word keywords</small>
      </article>
      <article className="rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-mid">
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="mid" /></i><b>Mid-tail</b></span>
        <strong>{data.overview.mid_tail_keywords}</strong>
        <small>3–4 word keywords</small>
      </article>
      <article className="rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-long">
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="long" /></i><b>Long-tail</b></span>
        <strong>{data.overview.long_tail_keywords}</strong>
        <small>5+ word keywords</small>
      </article>
      <article className="rrugc-stage0-used-kpi"><span>Used</span><strong>{data.overview.picked_keywords}</strong><small>Picked for use</small></article>
      <article className="rrugc-stage0-favorite-kpi"><span>Favorites</span><strong>{data.overview.favorite_keywords}</strong><small>Saved for later</small></article>
    </div>

    <div className="rrugc-stage0-toolbar">
      <KeywordSearchInput query={query} onQueryChange={onQueryChange} />
      <div className="rrugc-stage0-filter-groups">
        <div className="rrugc-stage0-usage-filter" role="group" aria-label="Filter Stage 0 keywords">
          <button
            type="button"
            className={usageFilter === "all" && tailFilter === "all" && !favoritesOnly && !query.trim() ? "active" : ""}
            aria-pressed={usageFilter === "all" && tailFilter === "all" && !favoritesOnly && !query.trim()}
            title="Clear all Stage 0 filters and show every keyword"
            onClick={onResetAll}
          ><Stage0ActionIcon kind="all" />All <b>{data.overview.total_keywords}</b></button>
          <button type="button" className={usageFilter === "unused" ? "active" : ""} aria-pressed={usageFilter === "unused"} onClick={() => onUsageFilterChange("unused")}><Stage0ActionIcon kind="unused" />Unused <b>{Math.max(0, data.overview.total_keywords - data.overview.picked_keywords)}</b></button>
          <button type="button" className={usageFilter === "used" ? "active" : ""} aria-pressed={usageFilter === "used"} onClick={() => onUsageFilterChange("used")}><Stage0ActionIcon kind="used" />Used <b>{data.overview.picked_keywords}</b></button>
        </div>
        <button
          type="button"
          className={"rrugc-stage0-favorite-filter" + (favoritesOnly ? " active" : "")}
          aria-pressed={favoritesOnly}
          onClick={() => onFavoritesOnlyChange(!favoritesOnly)}
        ><Stage0ActionIcon kind="favorite" />Favorites <b>{data.overview.favorite_keywords}</b></button>
        <div className="rrugc-stage0-tail-filter" role="group" aria-label="Filter keyword length">
          <button type="button" className={tailFilter === "short" ? "active" : ""} aria-pressed={tailFilter === "short"} title="2-word keywords" onClick={() => onTailFilterChange("short")}><KeywordTailIcon kind="short" />Short-tail <small>2</small></button>
          <button type="button" className={tailFilter === "mid" ? "active" : ""} aria-pressed={tailFilter === "mid"} title="3–4 word keywords" onClick={() => onTailFilterChange("mid")}><KeywordTailIcon kind="mid" />Mid-tail <small>3–4</small></button>
          <button type="button" className={tailFilter === "long" ? "active" : ""} aria-pressed={tailFilter === "long"} title="5 or more words" onClick={() => onTailFilterChange("long")}><KeywordTailIcon kind="long" />Long-tail <small>5+</small></button>
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
                  <Stage0ActionIcon kind={item.picked ? "picked" : "pick"} />
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
