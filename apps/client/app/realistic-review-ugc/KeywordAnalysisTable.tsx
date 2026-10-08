import { useEffect, useState } from "react";
import { KeywordSearchInput } from "./KeywordSearchInput";
import { RrugcStageHeader } from "./RrugcStageHeader";
import type {
  KeywordAnalysisSortBy,
  KeywordAnalysisSortDirection,
  KeywordTailFilter,
  KeywordUsageFilter,
  ScoutFeedbackAction,
  ScoutFeedbackScope,
} from "./api";
import type { KeywordVolume, KeywordVolumePage, KeywordVolumeTrendPoint } from "./types";

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
// Scout feedback controls are scoped to a keyword and its source Pin.

type KeywordTailKind = Exclude<KeywordTailFilter, "all">;

function KeywordTailIcon({ kind }: { kind: KeywordTailKind }) {
  const tokens = kind === "short"
    ? [[2, 8, 8, 7], [12, 8, 13, 7]]
    : kind === "mid"
      ? [[2, 4, 8, 6], [12, 4, 13, 6], [2, 14, 11, 6], [15, 14, 9, 6]]
      : [[2, 2, 8, 5], [12, 2, 13, 5], [2, 9.5, 11, 5], [15, 9.5, 9, 5], [2, 17, 6, 5], [10, 17, 14, 5]];
  return <svg viewBox="0 0 27 24" aria-hidden="true" focusable="false">
    {tokens.map(([x, y, width, height], index) => (
      <rect key={index} x={x} y={y} width={width} height={height} rx={height / 2} fill="currentColor" opacity={1 - index * .08} />
    ))}
  </svg>;
}

function formatCpc(value: number | null): string {
  return typeof value === "number" ? "$" + value.toFixed(2) : "—";
}

function formatChange(value: number | null | undefined): string {
  if (typeof value !== "number") return "—";
  return (value > 0 ? "+" : "") + value.toFixed(1) + "%";
}

function competitionTone(value: string | null): string {
  return (value || "unknown").toLowerCase();
}

function changeTone(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (typeof value !== "number" || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}

function keywordTailKind(keyword: string): KeywordTailKind {
  const count = keyword.trim().split(/\s+/).filter(Boolean).length;
  return count <= 2 ? "short" : count <= 4 ? "mid" : "long";
}

function keywordTailLabel(keyword: string): string {
  const kind = keywordTailKind(keyword);
  return kind === "short" ? "Short-tail" : kind === "mid" ? "Mid-tail" : "Long-tail";
}

function formatDetailDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatTableDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function providerLabel(value: string): string {
  return value === "aebrowse_google_ads" ? "Google Ads · AEBrowse" : value;
}

function formatTrendPeriod(value: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return value;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return date.toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
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

function detailTooltipPlacement(x: number, width = 720): "is-left-edge" | "is-centered" | "is-right-edge" {
  const ratio = width > 0 ? x / width : .5;
  if (ratio <= .24) return "is-left-edge";
  if (ratio >= .76) return "is-right-edge";
  return "is-centered";
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

function KeywordTrendChart({ item, onOpen }: { item: KeywordVolume; onOpen: () => void }) {
  const trend = item.trend ?? [];
  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen();
    }
  };

  if (trend.length < 2) {
    return <div
      className="rrugc-stage0-trend rrugc-stage0-trend-empty"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
      aria-label={"Open details for " + item.keyword + ". Monthly trend unavailable."}
    >
      <span>Trend unavailable</span>
      <small>Open keyword details</small>
    </div>;
  }

  const mini = makeChartGeometry(trend, 126, 38, 3);
  const large = makeChartGeometry(trend, 300, 118, 10);
  const volumes = trend.map(point => point.volume);
  const min = Math.min(...volumes);
  const max = Math.max(...volumes);
  const first = trend[0];
  const last = trend[trend.length - 1];

  return <div
    className="rrugc-stage0-trend"
    role="button"
    tabIndex={0}
    onClick={onOpen}
    onKeyDown={handleKeyDown}
    aria-label={"Open details and 12-month Google Ads trend for " + item.keyword}
  >
    <svg className="rrugc-stage0-sparkline" viewBox="0 0 126 38" preserveAspectRatio="none" aria-hidden="true">
      <path className="rrugc-stage0-spark-area" d={mini.area} />
      <path className="rrugc-stage0-spark-line" d={mini.line} />
      <circle cx={mini.dots[mini.dots.length - 1].x} cy={mini.dots[mini.dots.length - 1].y} r="2.2" />
    </svg>
    <span className="rrugc-stage0-trend-caption">{trend.length} mo</span>
    <div className="rrugc-stage0-trend-popover" role="tooltip">
      <div className="rrugc-stage0-trend-popover-head">
        <div><strong>{item.keyword}</strong><small>Google Ads monthly search volume</small></div>
        <b>{item.search_volume.toLocaleString()}</b>
      </div>
      <div className="rrugc-stage0-trend-changes">
        <span>3 mo <b className={changeTone(item.three_month_change_pct)}>{formatChange(item.three_month_change_pct)}</b></span>
        <span>YoY <b className={changeTone(item.yoy_change_pct)}>{formatChange(item.yoy_change_pct)}</b></span>
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

export function KeywordDetailModal({
  item,
  onClose,
}: {
  item: KeywordVolume | null;
  onClose: () => void;
}) {
  const [hoveredTrendIndex, setHoveredTrendIndex] = useState<number | null>(null);

  useEffect(() => {
    if (!item) return;
    setHoveredTrendIndex(null);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [item, onClose]);

  if (!item) return null;

  const trend = item.trend ?? [];
  const chart = trend.length > 1 ? makeChartGeometry(trend, 720, 180, 14) : null;
  const volumes = trend.map(point => point.volume);
  const low = volumes.length ? Math.min(...volumes) : null;
  const high = volumes.length ? Math.max(...volumes) : null;
  const tailKind = keywordTailKind(item.keyword);
  const wordCount = item.keyword.trim().split(/\s+/).filter(Boolean).length;

  return <div
    className="rrugc-stage0-detail-backdrop"
    onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}
  >
    <section
      className="rrugc-stage0-detail-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rrugc-stage0-detail-title"
    >
      <header className="rrugc-stage0-detail-header">
        <div>
          <small>KEYWORD DETAIL · GOOGLE ADS</small>
          <h2 id="rrugc-stage0-detail-title">{item.keyword}</h2>
          <div className="rrugc-stage0-detail-badges">
            <span className={"tail-" + tailKind}>{keywordTailLabel(item.keyword)} · {wordCount} words</span>
            <span className={item.favorite ? "is-favorite" : ""}>{item.favorite ? "★ Favorite" : "☆ Not favorite"}</span>
            <span className={item.picked ? "is-used" : ""}>{item.picked ? "Used" : "Unused"}</span>
          </div>
        </div>
        <button type="button" className="rrugc-stage0-detail-close" onClick={onClose} aria-label="Close keyword details">×</button>
      </header>

      <div className="rrugc-stage0-detail-body">
        <div className="rrugc-stage0-detail-hero">
          <div className="rrugc-stage0-detail-source">
            {item.source_image_url
              ? <a href={item.source_pin_url || item.source_image_url} target="_blank" rel="noreferrer">
                  <img src={item.source_image_url} alt={"Source for " + item.keyword} />
                  <span>Open source ↗</span>
                </a>
              : <div className="rrugc-stage0-detail-source-empty"><Icon name="search" /><span>No source image</span></div>}
          </div>
          <div className="rrugc-stage0-detail-summary">
            <div>
              <small>Data source</small>
              <strong>{providerLabel(item.provider)}</strong>
              <span>Checked {formatDetailDate(item.fetched_at)}</span>
            </div>
            <div>
              <small>Pinterest reference</small>
              {item.source_pin_url
                ? <a href={item.source_pin_url} target="_blank" rel="noreferrer">Open Pinterest source ↗</a>
                : <strong>—</strong>}
              <span>{item.request_count ? item.request_count.toLocaleString() + " provider requests" : "Provider request count unavailable"}</span>
            </div>
          </div>
        </div>

        <section className="rrugc-stage0-detail-metrics" aria-label="Keyword metrics">
          <article><small>Avg searches / mo</small><strong>{item.search_volume.toLocaleString()}</strong><span>Average monthly searches</span></article>
          <article><small>3-month change</small><strong className={changeTone(item.three_month_change_pct)}>{formatChange(item.three_month_change_pct)}</strong><span>vs 3 months ago</span></article>
          <article><small>YoY change</small><strong className={changeTone(item.yoy_change_pct)}>{formatChange(item.yoy_change_pct)}</strong><span>vs same month last year</span></article>
          <article><small>Competition</small><strong><span className={"rrugc-stage0-competition competition-" + competitionTone(item.competition)}>{item.competition || "—"}</span></strong><span>{typeof item.competition_index === "number" ? "Google Ads index " + item.competition_index : "Index unavailable"}</span></article>
          <article><small>Low CPC</small><strong>{formatCpc(item.cpc_low)}</strong><span>Top-of-page low bid</span></article>
          <article><small>High CPC</small><strong>{formatCpc(item.cpc_high)}</strong><span>Top-of-page high bid</span></article>
        </section>

        <section className="rrugc-stage0-detail-trend-panel">
          <div className="rrugc-stage0-detail-section-head">
            <div><small>TREND</small><h3>Monthly search volume</h3></div>
            {trend.length > 1 && <div className="rrugc-stage0-detail-trend-stats">
              <span>Low <b>{low?.toLocaleString()}</b></span>
              <span>High <b>{high?.toLocaleString()}</b></span>
              <span>Avg <b>{item.search_volume.toLocaleString()}</b></span>
            </div>}
          </div>
          {chart ? <>
            <div className="rrugc-stage0-detail-chart">
              <svg
                viewBox="0 0 720 180"
                preserveAspectRatio="none"
                aria-label={"Monthly search trend for " + item.keyword}
                onMouseLeave={() => setHoveredTrendIndex(null)}
              >
                <line x1="14" y1="45" x2="706" y2="45" />
                <line x1="14" y1="90" x2="706" y2="90" />
                <line x1="14" y1="135" x2="706" y2="135" />
                <path className="rrugc-stage0-spark-area" d={chart.area} />
                <path className="rrugc-stage0-spark-line" d={chart.line} />
                {chart.dots.map((dot, index) => <g
                  key={trend[index]?.period || index}
                  className={hoveredTrendIndex === index ? "is-active" : ""}
                  onMouseEnter={() => setHoveredTrendIndex(index)}
                  onFocus={() => setHoveredTrendIndex(index)}
                  onBlur={() => setHoveredTrendIndex(null)}
                  tabIndex={0}
                  aria-label={`${formatTrendPeriod(trend[index]?.period || "")}: ${trend[index]?.volume.toLocaleString()} searches`}
                >
                  <circle className="rrugc-stage0-detail-hit" cx={dot.x} cy={dot.y} r="13" />
                  <circle className="rrugc-stage0-detail-point" cx={dot.x} cy={dot.y} r="4" />
                </g>)}
              </svg>
              {hoveredTrendIndex !== null && chart.dots[hoveredTrendIndex] && trend[hoveredTrendIndex] && <div
                className={"rrugc-stage0-detail-chart-tooltip " + detailTooltipPlacement(chart.dots[hoveredTrendIndex].x)}
                style={{
                  left: `${(chart.dots[hoveredTrendIndex].x / 720) * 100}%`,
                  top: `${(chart.dots[hoveredTrendIndex].y / 180) * 100}%`,
                }}
              >
                <strong>{formatTrendPeriod(trend[hoveredTrendIndex].period)}</strong>
                <span>Search volume</span>
                <b>{trend[hoveredTrendIndex].volume.toLocaleString()}</b>
              </div>}
            </div>
            <div className="rrugc-stage0-detail-month-axis" aria-hidden="true">
              {trend.map(point => <span key={point.period}>{formatTrendPeriod(point.period)}</span>)}
            </div>
          </> : <div className="rrugc-stage0-detail-no-trend">
            <Icon name="chart" />
            <strong>No monthly history available</strong>
            <span>AEBrowse / Google Ads returned no monthly breakdown for this zero-volume or unsupported keyword.</span>
          </div>}
        </section>

        <details className="rrugc-stage0-detail-metadata">
          <summary>
            <span><small>RECORD</small><strong>Technical details</strong></span>
            <span className="rrugc-stage0-detail-metadata-summary">ID, provider account & timestamps</span>
          </summary>
          <dl>
            <div><dt>Keyword ID</dt><dd>{item.id}</dd></div>
            <div><dt>Provider</dt><dd>{providerLabel(item.provider)}</dd></div>
            <div><dt>Provider account</dt><dd>{item.provider_account || "—"}</dd></div>
            <div><dt>Customer ID</dt><dd>{item.provider_customer_id || "—"}</dd></div>
            <div><dt>Provider requests</dt><dd>{item.request_count?.toLocaleString() || "—"}</dd></div>
            <div><dt>Last checked</dt><dd>{formatDetailDate(item.fetched_at)}</dd></div>
            <div><dt>Last requested</dt><dd>{formatDetailDate(item.last_requested_at)}</dd></div>
            <div><dt>Added</dt><dd>{formatDetailDate(item.created_at)}</dd></div>
            <div><dt>Updated</dt><dd>{formatDetailDate(item.updated_at)}</dd></div>
            <div><dt>Favorite since</dt><dd>{formatDetailDate(item.favorite_at)}</dd></div>
            <div><dt>Used since</dt><dd>{formatDetailDate(item.picked_at)}</dd></div>
          </dl>
        </details>
      </div>
    </section>
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
  feedbackUpdatingIds,
  onFeedbackChange,
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
  feedbackUpdatingIds: Set<string>;
  onFeedbackChange: (keywordId: string, action: ScoutFeedbackAction, scope: ScoutFeedbackScope) => void;
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
  const activeOverviewFilter =
    favoritesOnly ? "favorites"
      : usageFilter === "used" ? "used"
        : tailFilter !== "all" ? tailFilter
          : "all";
  const [detailItem, setDetailItem] = useState<KeywordVolume | null>(null);
  const [feedbackScopes, setFeedbackScopes] = useState<Record<string, ScoutFeedbackScope>>({});

  const selectOverviewFilter = (filter: "all" | "short" | "mid" | "long" | "used" | "favorites") => {
    const nextUsage: KeywordUsageFilter = filter === "used" ? "used" : "all";
    const nextTail: KeywordTailFilter =
      filter === "short" || filter === "mid" || filter === "long" ? filter : "all";
    const nextFavoritesOnly = filter === "favorites";

    if (usageFilter !== nextUsage) onUsageFilterChange(nextUsage);
    if (tailFilter !== nextTail) onTailFilterChange(nextTail);
    if (favoritesOnly !== nextFavoritesOnly) onFavoritesOnlyChange(nextFavoritesOnly);
  };

  const handleQueryChange = (nextQuery: string) => {
    if (nextQuery.trim()) selectOverviewFilter("all");
    onQueryChange(nextQuery);
  };

  return <>
  <section className="rrugc-card rrugc-stage0 rrugc-stage0-v2">
    <RrugcStageHeader
      className="rrugc-stage0-heading"
      kicker="STAGE 0 · KEYWORD INTELLIGENCE"
      title="Analysis Keyword"
      description="Quote Scout discovers hat phrases and Stage 0 combines Pinterest context with Google Ads demand, CPC, competition and observed search-volume trends."
      actions={<span className="rrugc-stage0-live-badge"><i aria-hidden="true" />Live keyword data</span>}
    />

    <div className="rrugc-source-plan-kpis rrugc-stage0-kpis rrugc-stage0-kpis-restored" aria-label="Keyword overview filters">
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-total-kpi" + (activeOverviewFilter === "all" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "all"}
        onClick={() => selectOverviewFilter("all")}
      >
        <span>Total keywords</span>
        <strong>{data.overview.total_keywords.toLocaleString()}</strong>
        <small>{data.overview.total_search_volume.toLocaleString()} total monthly volume</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-short" + (activeOverviewFilter === "short" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "short"}
        onClick={() => selectOverviewFilter("short")}
      >
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="short" /></i><b>Short-tail</b></span>
        <strong>{data.overview.short_tail_keywords.toLocaleString()}</strong>
        <small>2-word keywords</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-mid" + (activeOverviewFilter === "mid" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "mid"}
        onClick={() => selectOverviewFilter("mid")}
      >
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="mid" /></i><b>Mid-tail</b></span>
        <strong>{data.overview.mid_tail_keywords.toLocaleString()}</strong>
        <small>3–4 word keywords</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-tail-kpi rrugc-stage0-tail-kpi-long" + (activeOverviewFilter === "long" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "long"}
        onClick={() => selectOverviewFilter("long")}
      >
        <span className="rrugc-stage0-tail-kpi-title"><i><KeywordTailIcon kind="long" /></i><b>Long-tail</b></span>
        <strong>{data.overview.long_tail_keywords.toLocaleString()}</strong>
        <small>5+ word keywords</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-used-kpi" + (activeOverviewFilter === "used" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "used"}
        onClick={() => selectOverviewFilter("used")}
      >
        <span>Used</span>
        <strong>{data.overview.picked_keywords.toLocaleString()}</strong>
        <small>Picked for use</small>
      </button>
      <button
        type="button"
        className={"rrugc-stage0-kpi-card rrugc-stage0-favorite-kpi" + (activeOverviewFilter === "favorites" ? " active" : "")}
        aria-pressed={activeOverviewFilter === "favorites"}
        onClick={() => selectOverviewFilter("favorites")}
      >
        <span>Favorites</span>
        <strong>{data.overview.favorite_keywords.toLocaleString()}</strong>
        <small>Saved opportunities</small>
      </button>
    </div>

    <div className="rrugc-stage0-controlbar">
      <div className="rrugc-stage0-searchbox"><KeywordSearchInput query={query} onQueryChange={handleQueryChange} /></div>
      <div className="rrugc-stage0-main-filters" role="group" aria-label="Keyword filters">
        <button type="button" className={activeOverviewFilter === "all" ? "active" : ""} onClick={onResetAll} title="Clear all Stage 0 filters and show every keyword">All</button>
        <button type="button" className={activeOverviewFilter === "favorites" ? "active" : ""} onClick={() => selectOverviewFilter("favorites")}><Icon name="star" filled={activeOverviewFilter === "favorites"} />Favorites</button>
        <button type="button" className={activeOverviewFilter === "short" ? "active" : ""} onClick={() => selectOverviewFilter("short")}>Short-tail</button>
        <button type="button" className={activeOverviewFilter === "mid" ? "active" : ""} onClick={() => selectOverviewFilter("mid")}>Mid-tail</button>
        <button type="button" className={activeOverviewFilter === "long" ? "active" : ""} onClick={() => selectOverviewFilter("long")}>Long-tail</button>
      </div>
    </div>

    <div className="rrugc-stage0-table-wrap">
      <table className="rrugc-stage0-table rrugc-stage0-keyword-table" aria-busy={loading}>
        <thead><tr>
          <th>Preview</th>
          <SortHeader column="keyword" label="Keyword" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <th>Trend</th>
          <SortHeader column="search_volume" label="Avg searches / mo" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="three_month_change" label="3-mo change" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="yoy_change" label="YoY change" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="competition" label="Competition" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="cpc" label="Low CPC ($)" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="high_cpc" label="High CPC ($)" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <SortHeader column="created_at" label="Created date" sortBy={sortBy} sortDirection={sortDirection} onSortChange={onSortChange} />
          <th>Action</th>
        </tr></thead>
        <tbody>
          {loading ? Array.from({ length: 6 }, (_, i) => <tr key={i} className="rrugc-stage0-skeleton-row"><td colSpan={11}><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-line" /></td></tr>) : data.items.map(item => (
            <tr key={item.id} className={item.picked ? "is-picked" : ""}>
              <td className="rrugc-stage0-source-image">
                {item.source_image_url ? <a href={item.source_pin_url || item.source_image_url} target="_blank" rel="noreferrer" title={"Open source for " + item.keyword}><img src={item.source_image_url} alt="" loading="lazy" decoding="async" /></a> : <span className="rrugc-stage0-source-empty">No image</span>}
              </td>
              <td className="rrugc-stage0-keyword-name">
                <button type="button" className="rrugc-stage0-keyword-link" onClick={() => setDetailItem(item)} title="Open keyword details">
                  <strong>{item.keyword}</strong>
                  <small>{providerLabel(item.provider)} · checked {new Date(item.fetched_at).toLocaleDateString()}</small>
                </button>
                {(item.scout_keyword_feedback === "blocked" || item.scout_pin_feedback === "blocked") ?
                  <span className="rrugc-scout-feedback-state is-blocked">Bỏ đề xuất</span> :
                 (item.scout_keyword_feedback === "suggested" || item.scout_pin_feedback === "suggested") ?
                  <span className="rrugc-scout-feedback-state is-suggested">Đã đề xuất</span> : null}
                <div className="rrugc-scout-feedback-actions" role="group" aria-label={"Scout feedback: " + item.keyword}>
                  <select aria-label={"Feedback target: " + item.keyword}
                    value={feedbackScopes[item.id] || (item.source_pin_url ? "both" : "keyword")}
                    disabled={feedbackUpdatingIds.has(item.id)}
                    onChange={event => setFeedbackScopes(current => ({ ...current, [item.id]: event.target.value as ScoutFeedbackScope }))}>
                    <option value="keyword">Keyword</option>
                    {item.source_pin_url && <option value="pin">Pin</option>}
                    {item.source_pin_url && <option value="both">Cả hai</option>}
                  </select>
                  <button type="button" className="is-suggested" disabled={feedbackUpdatingIds.has(item.id)}
                    onClick={() => onFeedbackChange(item.id, "suggested", feedbackScopes[item.id] || (item.source_pin_url ? "both" : "keyword"))}>Đề xuất</button>
                  <button type="button" className="is-blocked" disabled={feedbackUpdatingIds.has(item.id)}
                    onClick={() => onFeedbackChange(item.id, "blocked", feedbackScopes[item.id] || (item.source_pin_url ? "both" : "keyword"))}>Bỏ đề xuất</button>
                  {(item.scout_keyword_feedback !== "neutral" && item.scout_keyword_feedback ||
                    item.scout_pin_feedback !== "neutral" && item.scout_pin_feedback) &&
                    <button type="button" disabled={feedbackUpdatingIds.has(item.id)}
                      onClick={() => onFeedbackChange(item.id, "neutral", feedbackScopes[item.id] || (item.source_pin_url ? "both" : "keyword"))}>↶</button>}
                </div>
              </td>
              <td className="rrugc-stage0-trend-cell"><KeywordTrendChart item={item} onOpen={() => setDetailItem(item)} /></td>
              <td className="rrugc-stage0-volume"><strong>{item.search_volume.toLocaleString()}</strong><small>avg / month</small></td>
              <td className="rrugc-stage0-change"><strong className={changeTone(item.three_month_change_pct)}>{formatChange(item.three_month_change_pct)}</strong></td>
              <td className="rrugc-stage0-change"><strong className={changeTone(item.yoy_change_pct)}>{formatChange(item.yoy_change_pct)}</strong></td>
              <td><span className={"rrugc-stage0-competition competition-" + competitionTone(item.competition)}>{item.competition || "—"}</span>{typeof item.competition_index === "number" && <small className="rrugc-stage0-competition-index">{item.competition_index}</small>}</td>
              <td className="rrugc-stage0-cpc"><strong>{formatCpc(item.cpc_low)}</strong><small>low bid</small></td>
              <td className="rrugc-stage0-cpc"><strong>{formatCpc(item.cpc_high)}</strong><small>high bid</small></td>
              <td className="rrugc-stage0-created-date"><strong>{formatTableDate(item.created_at)}</strong></td>
              <td className="rrugc-stage0-pick-cell">
                <div className="rrugc-stage0-row-actions-v2">
                  <button type="button" className={"rrugc-stage0-icon-action favorite" + (item.favorite ? " active" : "")} aria-pressed={item.favorite} aria-label={(item.favorite ? "Remove favorite: " : "Add favorite: ") + item.keyword} title={item.favorite ? "Remove favorite" : "Favorite"} disabled={favoritingIds.has(item.id)} onClick={() => onFavoriteChange(item.id, !item.favorite)}><Icon name="star" filled={item.favorite} /></button>
                  <button type="button" className={"rrugc-stage0-icon-action pick" + (item.picked ? " active" : "")} aria-pressed={item.picked} aria-label={(item.picked ? "Mark unused: " : "Pick keyword: ") + item.keyword} title={item.picked ? "Used" : "Pick"} disabled={pickingIds.has(item.id)} onClick={() => onPickChange(item.id, !item.picked)}><Icon name={item.picked ? "check" : "plus"} /></button>
                </div>
              </td>
            </tr>
          ))}
          {!loading && data.items.length === 0 && <tr><td colSpan={11} className="rrugc-source-plan-empty">No keywords match these filters.</td></tr>}
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
  </section>
  <KeywordDetailModal item={detailItem} onClose={() => setDetailItem(null)} />
  </>;
}
