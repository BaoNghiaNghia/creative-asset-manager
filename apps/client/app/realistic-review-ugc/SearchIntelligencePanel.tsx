import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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

export function SearchIntelligenceModal({
  open, onClose, summary, loading, error,
}: {
  open: boolean;
  onClose: () => void;
  summary: QueryIntelligenceSummary | null;
  loading: boolean;
  error: string | null;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="rrugc-search-intelligence-backdrop" role="presentation"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose();
      }}>
      <section className="rrugc-search-intelligence-modal" role="dialog" aria-modal="true"
        aria-label="Search Intelligence" ref={dialogRef}>
        <header className="rrugc-search-intelligence-modal-header">
          <span>STAGE 0 / QUERY INTELLIGENCE</span>
          <button type="button" ref={closeRef} onClick={onClose} aria-label="Close Search Intelligence">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M5 5 19 19M19 5 5 19" /></svg>
          </button>
        </header>
        <div className="rrugc-search-intelligence-modal-content">
          <SearchIntelligencePanel summary={summary} loading={loading} error={error} />
        </div>
      </section>
    </div>,
    document.body,
  );
}
