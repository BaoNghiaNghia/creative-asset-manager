import { useState } from "react";
import type { KeywordVolumePage } from "./types";

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

function formatCpc(value: number | null): string {
  return typeof value === "number" ? "$" + value.toFixed(2) : "—";
}

function competitionTone(value: string | null): string {
  return (value || "unknown").toLowerCase();
}

export function KeywordAnalysisTable({
  data,
  query,
  loading = false,
  onPageChange,
  onPageSizeChange,
  onQueryChange,
}: {
  data: KeywordVolumePage;
  query: string;
  loading?: boolean;
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
    </div>

    <div className="rrugc-stage0-toolbar">
      <label className="rrugc-source-plan-search">
        <span className="sr-only">Search Stage 0 keywords</span>
        <input type="search" value={query} placeholder="Search keyword…" onChange={event => onQueryChange(event.target.value)} />
      </label>
      <div className="rrugc-stage0-filter" role="group" aria-label="Filter keyword volume">
        <button type="button" className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All</button>
        <button type="button" className={filter === "high" ? "active" : ""} onClick={() => setFilter("high")}>High competition</button>
        <button type="button" className={filter === "zero" ? "active" : ""} onClick={() => setFilter("zero")}>Zero volume</button>
      </div>
      <span className="rrugc-stage0-toolbar-count">{data.total} keywords</span>
    </div>

    <div className="rrugc-stage0-table-wrap">
      <table className="rrugc-stage0-table rrugc-stage0-keyword-table" aria-busy={loading}>
        <thead><tr><th>Image</th><th>Keyword</th><th>Search volume</th><th>Competition</th><th>CPC range</th><th>Last checked</th></tr></thead>
        <tbody>
          {loading ? Array.from({length: 5}, (_, i) => <tr key={i} className="rrugc-stage0-skeleton-row"><td colSpan={6}><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-line" /></td></tr>) : items.map(item => (
            <tr key={item.id}>
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
            </tr>
          ))}
          {!loading && items.length === 0 && <tr><td colSpan={6} className="rrugc-source-plan-empty">{query.trim() ? "No keyword matches this search." : "No keyword data yet. Start the separate quote-scout terminal and submit discovered keywords."}</td></tr>}
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
