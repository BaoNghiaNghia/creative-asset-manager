import { useMemo, useState } from "react";
import { SourceImageGroup, sourcePlanPageCount } from "./SourcePlanTable";
import type { SourcePlan, SourcePlanOverview } from "./types";

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

export function stage0DetectedQuote(plan: SourcePlan): string {
  const text = plan.visual_context?.embroidery_text
    ?.map(value => value.trim())
    .find(Boolean);
  if (text) return text;
  return plan.visual_context?.embroidery_identity?.trim() || "";
}

export function stage0KeywordPlan(plan: SourcePlan): string[] {
  const quote = stage0DetectedQuote(plan);
  if (!quote) return [];
  const normalized = quote.replace(/\s+/g, " ").trim();
  return Array.from(new Set([
    normalized,
    normalized + " embroidered hat",
    normalized + " cap",
    normalized + " trucker hat",
  ]));
}

function stage0AnalysisState(plan: SourcePlan) {
  const quote = stage0DetectedQuote(plan);
  if (plan.status === "failed") return { label: "Analysis failed", tone: "negative" };
  if (plan.status === "queued" || plan.status === "analyzing" || plan.status === "retry") {
    return { label: plan.status === "analyzing" ? "Analyzing text" : "Waiting analysis", tone: "working" };
  }
  if (!quote) return { label: "Needs quote review", tone: "warning" };
  return { label: "Ready for Quote Scout", tone: "positive" };
}

function quoteConfidence(plan: SourcePlan): string {
  const confidence = plan.visual_context?.confidence;
  if (typeof confidence !== "number") return "";
  return Math.round(Math.max(0, Math.min(1, confidence)) * 100) + "% confidence";
}

function Stage0SkeletonRows({ count }: { count: number }) {
  return <>{Array.from({ length: Math.min(8, Math.max(3, count)) }, (_, index) => (
    <tr key={"stage0-skeleton-" + index} className="rrugc-stage0-skeleton-row">
      <td><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-source" /></td>
      <td><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-line" /><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-short" /></td>
      <td><div className="rrugc-stage0-skeleton-chips"><i /><i /><i /></div></td>
      <td><span className="rrugc-stage0-skeleton rrugc-stage0-skeleton-status" /></td>
    </tr>
  ))}</>;
}

export function KeywordAnalysisTable({
  plans,
  total,
  overview,
  page,
  pageSize,
  query,
  loading = false,
  onPageChange,
  onPageSizeChange,
  onQueryChange,
}: {
  plans: SourcePlan[];
  total: number;
  overview: SourcePlanOverview;
  page: number;
  pageSize: number;
  query: string;
  loading?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onQueryChange: (query: string) => void;
}) {
  const [statusFilter, setStatusFilter] = useState<"all" | "ready" | "needs-analysis">("all");
  const pageCount = sourcePlanPageCount(total, pageSize);
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = total === 0 ? 0 : Math.min((page - 1) * pageSize + plans.length, total);

  const metrics = useMemo(() => {
    const quoteDetected = plans.filter(plan => Boolean(stage0DetectedQuote(plan))).length;
    const keywordReady = plans.filter(plan => stage0KeywordPlan(plan).length > 0).length;
    return {
      quoteDetected,
      keywordReady,
      needsAnalysis: Math.max(0, plans.length - keywordReady),
    };
  }, [plans]);

  const visiblePlans = useMemo(() => plans.filter(plan => {
    const ready = stage0KeywordPlan(plan).length > 0;
    if (statusFilter === "ready") return ready;
    if (statusFilter === "needs-analysis") return !ready;
    return true;
  }), [plans, statusFilter]);

  return <section className="rrugc-card rrugc-stage0">
    <div className="rrugc-section-heading rrugc-stage0-heading">
      <div>
        <small>DRIVE → QUOTE ANALYSIS → PINTEREST KEYWORDS</small>
        <h2>Stage 0 · Analysis Keyword</h2>
        <p>Analyze embroidery text first, then prepare quote-focused Pinterest queries for a second scout running in parallel with the existing reference scout.</p>
      </div>
      <div className="rrugc-stage0-heading-meta">
        <span className="rrugc-stage0-lane"><i aria-hidden="true" />Quote Scout · parallel lane</span>
        <small>Stage 1 remains the visual-context reference scout.</small>
      </div>
    </div>

    <div className="rrugc-stage0-flow" aria-label="Quote scout workflow">
      <div><b>01</b><span><strong>Read embroidery</strong><small>Extract exact quote/text</small></span></div>
      <i aria-hidden="true">→</i>
      <div><b>02</b><span><strong>Build keywords</strong><small>Exact + hat variants</small></span></div>
      <i aria-hidden="true">→</i>
      <div><b>03</b><span><strong>Quote Scout</strong><small>Pinterest runs in parallel</small></span></div>
      <i aria-hidden="true">→</i>
      <div><b>04</b><span><strong>Save results</strong><small>Quote library in system</small></span></div>
    </div>

    <div className="rrugc-source-plan-kpis rrugc-stage0-kpis">
      <article><span>Embroidery groups</span><strong>{overview.embroidery_groups}</strong><small>Drive source groups</small></article>
      <article><span>Quote detected</span><strong>{metrics.quoteDetected}</strong><small>Current page</small></article>
      <article><span>Keyword ready</span><strong>{metrics.keywordReady}</strong><small>Ready for Quote Scout</small></article>
      <article><span>Needs analysis</span><strong>{metrics.needsAnalysis}</strong><small>Current page</small></article>
    </div>

    <div className="rrugc-stage0-toolbar">
      <label className="rrugc-source-plan-search">
        <span className="sr-only">Search quote analysis groups</span>
        <input
          type="search"
          value={query}
          placeholder="Search source file, folder or embroidery…"
          onChange={event => onQueryChange(event.target.value)}
        />
      </label>
      <div className="rrugc-stage0-filter" role="group" aria-label="Filter keyword analysis">
        <button type="button" className={statusFilter === "all" ? "active" : ""} onClick={() => setStatusFilter("all")}>All</button>
        <button type="button" className={statusFilter === "ready" ? "active" : ""} onClick={() => setStatusFilter("ready")}>Keyword ready</button>
        <button type="button" className={statusFilter === "needs-analysis" ? "active" : ""} onClick={() => setStatusFilter("needs-analysis")}>Needs analysis</button>
      </div>
      <span className="rrugc-stage0-toolbar-count">{total} embroidery groups</span>
    </div>

    <div className="rrugc-stage0-table-wrap">
      <table className="rrugc-stage0-table" aria-busy={loading}>
        <thead><tr><th>Embroidery group</th><th>Detected quote</th><th>Keyword plan</th><th>Analysis status</th></tr></thead>
        <tbody>
          {loading ? <Stage0SkeletonRows count={pageSize} /> : visiblePlans.map((plan, rowIndex) => {
            const quote = stage0DetectedQuote(plan);
            const keywords = stage0KeywordPlan(plan);
            const state = stage0AnalysisState(plan);
            const confidence = quoteConfidence(plan);
            return <tr key={plan.id}>
              <td className="rrugc-stage0-source-cell">
                <div className="rrugc-stage0-source">
                  <SourceImageGroup plan={plan} priority={rowIndex < 4} />
                  <span>
                    <strong title={plan.source_name}>{plan.source_name}</strong>
                    <small>{plan.embroidery_group_size} source {plan.embroidery_group_size === 1 ? "image" : "images"} · same embroidery</small>
                  </span>
                </div>
              </td>
              <td className="rrugc-stage0-quote-cell">
                {quote ? <>
                  <blockquote>“{quote}”</blockquote>
                  <div className="rrugc-stage0-quote-meta">
                    <span>Exact text</span>
                    {confidence && <span>{confidence}</span>}
                  </div>
                </> : <div className="rrugc-stage0-empty-copy">
                  <strong>No quote detected yet</strong>
                  <small>Waiting for embroidery text analysis.</small>
                </div>}
              </td>
              <td className="rrugc-stage0-keyword-cell">
                {keywords.length ? <div className="rrugc-stage0-keyword-plan">
                  {keywords.map((keyword, index) => <span key={keyword} className={index === 0 ? "is-exact" : ""}>
                    <small>{index === 0 ? "Exact" : index === 1 ? "Embroidery" : index === 2 ? "Cap" : "Trucker"}</small>
                    <b>{keyword}</b>
                  </span>)}
                </div> : <small className="rrugc-stage0-waiting">Keyword plan will appear after quote analysis.</small>}
              </td>
              <td className="rrugc-stage0-status-cell">
                <span className={"rrugc-stage0-status tone-" + state.tone}><i aria-hidden="true" />{state.label}</span>
                {keywords.length > 0 ? <>
                  <strong>{keywords.length} search queries</strong>
                  <small>Exact phrase is highest priority.</small>
                </> : <>
                  <strong>0 search queries</strong>
                  <small>Quote Scout will not run this group yet.</small>
                </>}
              </td>
            </tr>;
          })}
          {!loading && visiblePlans.length === 0 && <tr><td colSpan={4} className="rrugc-source-plan-empty">
            {statusFilter === "all" ? "No embroidery groups match this search." : "No groups match this analysis filter on the current page."}
          </td></tr>}
        </tbody>
      </table>
    </div>

    <div className="rrugc-source-pagination rrugc-stage0-pagination">
      <span>{start}–{end} of {total}</span>
      <div className="rrugc-source-page-controls">
        <button type="button" disabled={loading || page <= 1} onClick={() => onPageChange(1)} aria-label="Stage 0 first page">«</button>
        <button type="button" disabled={loading || page <= 1} onClick={() => onPageChange(Math.max(1, page - 1))} aria-label="Stage 0 previous page">‹</button>
        <strong>Page {page} / {pageCount}</strong>
        <button type="button" disabled={loading || page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, page + 1))} aria-label="Stage 0 next page">›</button>
        <button type="button" disabled={loading || page >= pageCount} onClick={() => onPageChange(pageCount)} aria-label="Stage 0 last page">»</button>
      </div>
      <label>Rows<select aria-label="Stage 0 rows per page" value={pageSize} disabled={loading} onChange={event => onPageSizeChange(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    </div>
  </section>;
}
