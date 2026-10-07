import { KeywordSearchInput } from "./KeywordSearchInput";
import { RrugcStageHeader } from "./RrugcStageHeader";
import type {
  KeywordAnalysisSortBy,
  KeywordAnalysisSortDirection,
  KeywordTailFilter,
  KeywordUsageFilter,
} from "./api";
import type { KeywordVolume, KeywordVolumePage, KeywordVolumeTrendPoint } from "./types";

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

function formatCompact(value: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatCpc(value: number | null): string {
  return typeof value === "number" ? "$" + value.toFixed(2) : "—";
}

function formatAverageCpc(value: number | null | undefined): string {
  return typeof value === "number" ? "$" + value.toFixed(2) : "—";
}

function competitionTone(value: string | null): string {
  return (value || "unknown").toLowerCase();
}

function Icon({
  name,
  filled = false,
}: {
  name: "search" | "star" | "chart" | "money" | "competition" | "check" | "plus";
  filled?: boolean;
}) {
  if (name === "search") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.2" /><path d="m15.2 15.2 4.3 4.3" /></svg>;
  if (name === "star") return <svg viewBox="0 0 24 24" aria-hidden="true" fill={filled ? "currentColor" : "none"}><path d="m12 2.8 2.8 5.7 6.3.9-4.6 4.5 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.5 6.3-.9z" /></svg>;
  if (name === "chart") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18V8m5 10v-5m5 5V5m5 13v-8" /></svg>;
  if (name === "money") return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M14.8 8.8c-.5-.8-1.5-1.2-2.7-1.2-1.5 0-2.6.7-2.6 1.8 0 2.8 5.4 1.1 5.4 4 0 1.2-1.1 2-2.8 2-1.4 0-2.5-.5-3-1.4M12 6.2v11.6" /></svg>;
  if (name === "competition") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h3V9H4zm6 0h4V5h-4zm7 0h3v-6h-3z" /></svg>;
  if (name === "check") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6.5 12.4 3.3 3.3 7.7-8" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>;
}

function makeChartGeometry(points: KeywordVolumeTrendPoint[], width: number, height: number, pad = 4) {
  const source = points.length > 1 ? points : points.length === 1 ? [points[0], points[0]] : [];
  if (!source.length) return { line: "", area: "", dots: [] as Array<{ x: number; y: number }> };
  const values = source.map(point => Math.max(0, point.volume));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const usableW = width - pad * 2;
  const usableH = height - pad * 2;
  const dots = source.map((point, index) => ({
    x: pad + (source.length === 1 ? usableW / 2 : usableW * index / Math.max(1, source.length - 1)),
    y: pad + usableH - ((Math.max(0, point.volume) - min) / range) * usableH,
  }));
  const line = dots.map((dot, index) => (index ? "L" : "M") + dot.x.toFixed(1) + " " + dot.y.toFixed(1)).join(" ");
  const area = line
    ? line + " L " + dots[dots.length - 1].x.toFixed(1) + " " + (height - pad) + " L " + dots[0].x.toFixed(1) + " " + (height - pad) + " Z"
    : "";
  return { line, area, dots };
}

function KeywordTrendChart({ item }: { item: KeywordVolume }) {
  const trend = item.trend?.length ? item.trend : [{ period: item.fetched_at.slice(0, 10), volume: item.search_volume }];
  const mini = makeChartGeometry(trend, 126, 38, 3);
  const large = makeChartGeometry(trend, 300, 118, 10);
  const volumes = trend.map(point => point.volume);
  const min = Math.min(...volumes);
  const max = Math.max(...volumes);
  const first = trend[0];
  const last = trend[trend.length - 1];

  return <div className="rrugc-stage0-trend" tabIndex={0} aria-label={"Search-volume trend for " + item.keyword}>
    <svg className="rrugc-stage0-sparkline" viewBox="0 0 126 38" preserveAspectRatio="none" aria-hidden="true">
      <path className="rrugc-stage0-spark-area" d={mini.area} />
      <path className="rrugc-stage0-spark-line" d={mini.line} />
      {mini.dots.length > 1 && <circle cx={mini.dots[mini.dots.length - 1].x} cy={mini.dots[mini.dots.length - 1].y} r="2.2" />}
    </svg>
    <span className="rrugc-stage0-trend-caption">{trend.length > 1 ? trend.length + " points" : "Latest"}</span>
    <div className="rrugc-stage0-trend-popover" role="tooltip">
      <div className="rrugc-stage0-trend-popover-head">
        <div><strong>{item.keyword}</strong><small>Search-volume trend</small></div>
        <b>{item.search_volume.toLocaleString()}</b>
      </div>
      <svg viewBox="0 0 300 118" preserveAspectRatio="none" aria-hidden="true">
        <line x1="10" y1="30" x2="290" y2="30" />
        <line x1="10" y1="64" x2="290" y2="64" />
        <line x1="10" y1="98" x2="290" y2="98" />
        <path className="rrugc-stage0-spark-area" d={large.area} />
        <path className="rrugc-stage0-spark-line" d={large.line} />
        {large.dots.map((dot, index) => <circle key={index} cx={dot.x} cy={dot.y} r="2.5" />)}
      </svg>
      <div className="rrugc-stage0-trend-axis"><span>{first.period}</span><span>{last.period}</span></div>
      <div className="rrugc-stage0-trend-meta"><span>Low <b>{min.toLocaleString()}</b></span><span>High <b>{max.toLocaleString()}</b></span><span>Current <b>{item.search_volume.toLocaleString()}</b></span></div>
    </div>
  </div>;
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
  return <th aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
    <button
      type="button"
      className={"rrugc-stage0-sort" + (active ? " active" : "")}
      onClick={() => onSortChange(column)}
      aria-label={"Sort by " + label + " " + (active && sortDirection === "asc" ? "descending" : "ascending")}
    >
      <span>{label}</span>
      <span aria-hidden="true">{active ? (sortDirection === "asc" ? "↑" : "↓") : "↕"}</span>
    </button>
  </th>;
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
  const activeMainFilter = favoritesOnly ? "favorites" : tailFilter;

  return <section className="rrugc-card rrugc-stage0 rrugc-stage0-v2">
    <RrugcStageHeader
      className="rrugc-stage0-heading"
      kicker="STAGE 0 · KEYWORD INTELLIGENCE"
      title="Analysis Keyword"
      description="Quote Scout discovers hat phrases and Stage 0 combines Pinterest context with Google Ads demand, CPC, competition and observed search-volume trends."
      actions={<span className="rrugc-stage0-live-badge"><i aria-hidden="true" />Live keyword data</span>}
    />

    <div className="rrugc-stage0-metrics" aria-label="Keyword overview">
      <article><span className="rrugc-stage0-metric-icon"><Icon name="search" /></span><div><small>Keywords</small><strong>{data.overview.total_keywords.toLocaleString()}</strong><em>{formatCompact(data.overview.total_search_volume)} total monthly volume</em></div></article>
      <article><span className="rrugc-stage0-metric-icon is-star"><Icon name="star" filled /></span><div><small>Favorites</small><strong>{data.overview.favorite_keywords.toLocaleString()}</strong><em>Saved opportunities</em></div></article>
      <article><span className="rrugc-stage0-metric-icon"><Icon name="chart" /></span><div><small>Average volume</small><strong>{Math.round(data.overview.average_search_volume ?? (data.overview.total_keywords ? data.overview.total_search_volume / data.overview.total_keywords : 0)).toLocaleString()}</strong><em>Searches / month</em></div></article>
      <article><span className="rrugc-stage0-metric-icon"><Icon name="money" /></span><div><small>Average CPC</small><strong>{formatAverageCpc(data.overview.average_cpc)}</strong><em>Average bid midpoint</em></div></article>
      <article><span className="rrugc-stage0-metric-icon"><Icon name="competition" /></span><div><small>High competition</small><strong>{data.overview.high_competition.toLocaleString()}</strong><em>{data.overview.total_keywords ? Math.round(data.overview.high_competition / data.overview.total_keywords * 100) : 0}% of keywords</em></div></article>
    </div>

    <div className="rrugc-stage0-controlbar">
      <div className="rrugc-stage0-searchbox"><KeywordSearchInput query={query} onQueryChange={onQueryChange} /></div>
      <div className="rrugc-stage0-main-filters" role="group" aria-label="Keyword filters">
        <button type="button" className={activeMainFilter === "all" && !query.trim() ? "active" : ""} onClick={onResetAll} title="Clear all Stage 0 filters and show every keyword">All</button>
        <button type="button" className={favoritesOnly ? "active" : ""} onClick={() => onFavoritesOnlyChange(!favoritesOnly)}><Icon name="star" filled={favoritesOnly} />Favorites</button>
        <button type="button" className={!favoritesOnly && tailFilter === "short" ? "active" : ""} onClick={() => onTailFilterChange(tailFilter === "short" ? "all" : "short")}>Short-tail</button>
        <button type="button" className={!favoritesOnly && tailFilter === "mid" ? "active" : ""} onClick={() => onTailFilterChange(tailFilter === "mid" ? "all" : "mid")}>Mid-tail</button>
        <button type="button" className={!favoritesOnly && tailFilter === "long" ? "active" : ""} onClick={() => onTailFilterChange(tailFilter === "long" ? "all" : "long")}>Long-tail</button>
      </div>
      <div className="rrugc-stage0-usage-switch" role="group" aria-label="Keyword usage filter">
        <button type="button" className={usageFilter === "all" ? "active" : ""} onClick={() => onUsageFilterChange("all")}>All usage</button>
        <button type="button" className={usageFilter === "unused" ? "active" : ""} onClick={() => onUsageFilterChange("unused")}>Unused</button>
        <button type="button" className={usageFilter === "used" ? "active" : ""} onClick={() => onUsageFilterChange("used")}>Used</button>
      </div>
    </div>

    <div className="rrugc-stage0-table-wrap">
      <table className="rrugc-stage0-table rrugc-stage0-keyword-table" aria-busy={loading}>
        <thead><tr>
          <th>Preview</th>
          <SortHeader column="keyword" label="Keyword" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <th>Trend</th>
          <SortHeader column="search_volume" label="Volume" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="cpc" label="CPC" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="competition" label="Competition" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <th>Action</th>
        </tr></thead>
        <tbody>
          {loading ? Array.from({ length: 6 }, (_, i) => <tr key={i} className="rrugc-stage0-skeleton-row"><td colSpan={7}><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-line" /></td></tr>) : data.items.map(item => (
            <tr key={item.id} className={item.picked ? "is-picked" : ""}>
              <td className="rrugc-stage0-source-image">
                {item.source_image_url ? <a href={item.source_pin_url || item.source_image_url} target="_blank" rel="noreferrer" title={"Open source for " + item.keyword}><img src={item.source_image_url} alt="" loading="lazy" decoding="async" /></a> : <span className="rrugc-stage0-source-empty">No image</span>}
              </td>
              <td className="rrugc-stage0-keyword-name">
                <strong>{item.keyword}</strong>
                <small>{item.provider === "aebrowse_google_ads" ? "Google Ads · AEBrowse" : item.provider} · checked {new Date(item.fetched_at).toLocaleDateString()}</small>
              </td>
              <td className="rrugc-stage0-trend-cell"><KeywordTrendChart item={item} /></td>
              <td className="rrugc-stage0-volume"><strong>{item.search_volume.toLocaleString()}</strong><small>searches / mo</small></td>
              <td className="rrugc-stage0-cpc"><strong>{formatCpc(item.cpc_low)}–{formatCpc(item.cpc_high)}</strong><small>bid range</small></td>
              <td><span className={"rrugc-stage0-competition competition-" + competitionTone(item.competition)}>{item.competition || "—"}</span></td>
              <td className="rrugc-stage0-pick-cell">
                <div className="rrugc-stage0-row-actions-v2">
                  <button type="button" className={"rrugc-stage0-icon-action favorite" + (item.favorite ? " active" : "")} aria-pressed={item.favorite} aria-label={(item.favorite ? "Remove favorite: " : "Add favorite: ") + item.keyword} title={item.favorite ? "Remove favorite" : "Favorite"} disabled={favoritingIds.has(item.id)} onClick={() => onFavoriteChange(item.id, !item.favorite)}><Icon name="star" filled={item.favorite} /></button>
                  <button type="button" className={"rrugc-stage0-icon-action pick" + (item.picked ? " active" : "")} aria-pressed={item.picked} aria-label={(item.picked ? "Mark unused: " : "Pick keyword: ") + item.keyword} title={item.picked ? "Used" : "Pick"} disabled={pickingIds.has(item.id)} onClick={() => onPickChange(item.id, !item.picked)}><Icon name={item.picked ? "check" : "plus"} /></button>
                </div>
              </td>
            </tr>
          ))}
          {!loading && data.items.length === 0 && <tr><td colSpan={7} className="rrugc-source-plan-empty">No keywords match these filters.</td></tr>}
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
