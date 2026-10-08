import { useState } from "react";
import type { QueryIntelligenceSummary } from "./api";

const LABELS: Record<string, string> = {
  suggested: "Đề xuất", style: "Phong cách tương tự",
  product: "Ý tưởng mũ", explore: "Khám phá mới",
};

export function SearchIntelligencePanel({
  summary, loading, error,
}: {
  summary: QueryIntelligenceSummary | null;
  loading: boolean;
  error: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  return <aside className="rrugc-search-intelligence" aria-label="Search Intelligence">
    <div className="rrugc-search-intelligence-top">
      <div>
        <strong>Search Intelligence</strong>
        <span>Dynamic Query Pool · Adaptive Learning · Shared across Scout machines</span>
      </div>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? "Thu gọn" : "Xem hiệu suất truy vấn"}
      </button>
    </div>
    {error && <p className="rrugc-search-intelligence-error" role="status">Chưa tải được Search Intelligence: {error}</p>}
    <div className="rrugc-search-intelligence-metrics">
      <div><small>Queries</small><strong>{summary?.total_queries ?? (loading ? "…" : "—")}</strong></div>
      <div><small>Completed cycles</small><strong>{summary?.cycles_completed ?? "—"}</strong></div>
      <div><small>New keywords</small><strong>{summary?.new_keywords ?? "—"}</strong></div>
      <div><small>Duplicate pins</small><strong>{summary?.duplicate_pins ?? "—"}</strong></div>
      <div><small>Leased searches</small><strong>{summary?.active_leases ?? "—"}</strong></div>
    </div>
    {expanded && <div className="rrugc-search-intelligence-expanded">
      <div className="rrugc-search-intelligence-lanes">
        {(summary?.lanes || []).map(lane => <div key={lane.lane}>
          <strong>{LABELS[lane.lane] || lane.lane}</strong>
          <span>{lane.queries} queries · {lane.cycles} cycles</span>
        </div>)}
      </div>
      <div className="rrugc-search-intelligence-table-wrap">
        <table>
          <thead><tr><th>Pinterest query</th><th>Lane</th><th>Cycles</th><th>New keywords</th><th>Duplicates</th><th>Last search</th></tr></thead>
          <tbody>{(summary?.recent || []).map(row => <tr key={row.query}>
            <td title={row.query}>{row.query}</td>
            <td>{LABELS[row.lane] || row.lane}</td>
            <td>{row.cycles}</td>
            <td>{row.new_keywords}</td>
            <td>{row.duplicate_pins}</td>
            <td>{row.last_searched_at ? new Date(row.last_searched_at).toLocaleString() : "Not run"}</td>
          </tr>)}
          {!summary?.recent?.length && <tr><td colSpan={6}>Chưa có lịch sử tìm kiếm. Dữ liệu sẽ xuất hiện khi Keyword Scout chạy phiên bản mới.</td></tr>}</tbody>
        </table>
      </div>
    </div>}
  </aside>;
}
