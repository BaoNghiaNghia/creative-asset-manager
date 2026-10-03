import { useEffect, useRef, useState } from "react";
import type { SourcePlanSortBy, SourcePlanSortDirection } from "./api";
import type { ReferenceManualLabel, SourcePlan, SourcePlanGroupImage, SourcePlanReferencePreview } from "./types";

const SOURCE_ROOT_FOLDER_ID = "1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX";
const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;

export function sourcePlanProgressPercent(plan: Pick<SourcePlan, "progress_count" | "target_count">): number {
  if (plan.target_count <= 0) return 0;
  return Math.min(100, Math.round((plan.progress_count / plan.target_count) * 100));
}

export function sourcePlanPageCount(totalRows: number, pageSize: number): number {
  return Math.max(1, Math.ceil(Math.max(0, totalRows) / Math.max(1, pageSize)));
}

export function sourcePlanContextSummary(plan: SourcePlan): string {
  const summary = plan.visual_context?.summary?.trim();
  if (summary) return summary;
  const scene = plan.visual_context?.scene_hints?.find(Boolean);
  const theme = plan.visual_context?.themes?.find(Boolean);
  if (scene && theme) return theme + " · " + scene;
  return scene || theme || (plan.status === "ready" ? "Context ready" : "Waiting for AI context analysis");
}

function planStatusLabel(plan: SourcePlan): string {
  if (plan.status === "ready" && plan.progress_count >= plan.target_count) return "Complete";
  if (plan.status === "ready" && plan.scout_status === "busy") return "Scouting";
  if (plan.status === "ready" && plan.auto_scout) return "Ready";
  if (plan.status === "queued") return "Queued";
  if (plan.status === "analyzing") return "Analyzing";
  if (plan.status === "retry") return "Retrying";
  if (plan.status === "failed") return "Failed";
  if (plan.status === "missing") return "Missing";
  return plan.status.replaceAll("_", " ");
}

function planTone(plan: SourcePlan): string {
  if (plan.status === "missing" || plan.status === "failed") return "negative";
  if (plan.progress_count >= plan.target_count && plan.target_count > 0) return "positive";
  if (plan.status === "ready") return plan.scout_status === "busy" ? "working" : "ready";
  return "neutral";
}

function scoutStatusPresentation(plan: SourcePlan): { label: string; tone: string } {
  const status = plan.scout_status || "offline";
  if (
    status === "error"
    && plan.auto_scout
    && plan.progress_count < plan.target_count
  ) {
    return { label: "retrying", tone: "busy" };
  }
  return { label: status.replaceAll("_", " "), tone: status };
}

function sourcePlanSortDirectionLabel(
  sortBy: SourcePlanSortBy,
  direction: SourcePlanSortDirection,
): string {
  if (sortBy === "source" || sortBy === "status") {
    return direction === "asc" ? "A → Z" : "Z → A";
  }
  if (sortBy === "group_size") {
    return direction === "asc" ? "Small → large" : "Large → small";
  }
  return direction === "asc" ? "Oldest → newest" : "Newest → oldest";
}

function sourceMeta(plan: SourcePlan): string {
  const dimensions = plan.source_width && plan.source_height
    ? plan.source_width + "×" + plan.source_height
    : "";
  const size = plan.source_size_bytes
    ? (plan.source_size_bytes / (1024 * 1024)).toFixed(plan.source_size_bytes > 10 * 1024 * 1024 ? 0 : 1) + " MB"
    : "";
  return [dimensions, size].filter(Boolean).join(" · ");
}

type SourceThumbItem = Pick<
  SourcePlanGroupImage,
  "id" | "source_name" | "source_preview_url" | "source_web_url"
>;

function SourceImageThumb({ source }: { source: SourceThumbItem }) {
  const [loaded, setLoaded] = useState(false);
  const media = <span className={"rrugc-source-thumb-media " + (loaded ? "is-loaded" : "is-loading")}>
    {!loaded && <span className="rrugc-source-thumb-skeleton" aria-hidden="true" />}
    <img
      src={source.source_preview_url}
      alt={source.source_name}
      loading="lazy"
      decoding="async"
      onLoad={() => setLoaded(true)}
      onError={() => setLoaded(true)}
    />
  </span>;

  return source.source_web_url
    ? <a href={source.source_web_url} target="_blank" rel="noreferrer" className="rrugc-source-thumb" title={"Open " + source.source_name + " in Google Drive"}>{media}</a>
    : <span className="rrugc-source-thumb" title={source.source_name}>{media}</span>;
}

function SourceImageGroup({ plan }: { plan: SourcePlan }) {
  const sources: SourceThumbItem[] = plan.source_group_images?.length
    ? plan.source_group_images
    : [plan];

  return <div className="rrugc-source-group-track" aria-label={sources.length + " source images with the same embroidery"}>
    {sources.map(source => <SourceImageThumb key={source.id} source={source} />)}
  </div>;
}


export function ReferenceReviewModal({
  plan,
  reviewingReferenceIds,
  onSetReferenceFeedback,
  onClose,
}: {
  plan: SourcePlan;
  reviewingReferenceIds: ReadonlySet<string>;
  onSetReferenceFeedback: (
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
    label: ReferenceManualLabel,
  ) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return <div className="rrugc-source-review-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className="rrugc-source-review-modal" role="dialog" aria-modal="true" aria-labelledby={"rrugc-source-review-title-" + plan.id}>
      <header className="rrugc-source-review-header">
        <div>
          <small>REFERENCE REVIEW</small>
          <h2 id={"rrugc-source-review-title-" + plan.id}>{plan.source_name}</h2>
          <p>{plan.reference_previews.length} images · {plan.approved_count} analyzed · {plan.pending_ai_count} pending AI</p>
        </div>
        <button type="button" className="rrugc-source-review-close" aria-label="Close reference review" onClick={onClose}>×</button>
      </header>
      <div className="rrugc-source-review-masonry">
        {plan.reference_previews.map((reference, index) => {
          const reviewing = reviewingReferenceIds.has(reference.id);
          const pending = reference.status === "analysis_queued" || reference.status === "analyzing";
          return <article
            key={reference.id}
            className={
              "rrugc-source-review-card status-" + reference.status
              + (pending ? " is-pending-ai" : "")
              + (reference.picked ? " is-picked" : "")
              + (reference.rejected ? " is-rejected" : "")
            }
          >
            <div className="rrugc-source-review-image">
              <img src={reference.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
              <span className="rrugc-source-review-index">{index + 1}</span>
              {reference.status === "analysis_failed" && <span className="rrugc-source-review-failed" title="AI analysis failed">!</span>}
              <div className="rrugc-source-review-votes" role="group" aria-label={"Reference " + (index + 1) + " feedback for " + plan.source_name}>
                <button
                  type="button"
                  className="rrugc-source-review-vote is-good"
                  aria-label={(reference.picked ? "Clear suitable mark for " : "Mark suitable ") + "reference " + (index + 1)}
                  aria-pressed={reference.picked}
                  disabled={!plan.campaign_id || reviewing}
                  onClick={() => onSetReferenceFeedback(plan, reference, "good")}
                >{reviewing ? "…" : "✓"}</button>
                <button
                  type="button"
                  className="rrugc-source-review-vote is-bad"
                  aria-label={(reference.rejected ? "Clear unsuitable mark for " : "Mark unsuitable ") + "reference " + (index + 1)}
                  aria-pressed={reference.rejected}
                  disabled={!plan.campaign_id || reviewing}
                  onClick={() => onSetReferenceFeedback(plan, reference, "bad")}
                >{reviewing ? "…" : "×"}</button>
                <button
                  type="button"
                  className="rrugc-source-review-vote is-ai"
                  aria-label={"Mark AI-generated reference " + (index + 1)}
                  aria-pressed={false}
                  disabled={!plan.campaign_id || reviewing}
                  title="AI-generated · reject from real refs · train AI + negative"
                  onClick={() => onSetReferenceFeedback(plan, reference, "ai")}
                >{reviewing ? "…" : "AI"}</button>
              </div>
            </div>
            <footer>
              <span>{pending ? "Pending analysis" : reference.status.replaceAll("_", " ")}</span>
              <a href={reference.pin_url} target="_blank" rel="noreferrer">Pinterest ↗</a>
            </footer>
          </article>;
        })}
      </div>
    </section>
  </div>;
}

function ReferenceSlider({
  plan,
  reviewingReferenceIds,
  onSetReferenceFeedback,
}: {
  plan: SourcePlan;
  reviewingReferenceIds: ReadonlySet<string>;
  onSetReferenceFeedback: (
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
    label: ReferenceManualLabel,
  ) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const references = plan.reference_previews;

  function move(direction: -1 | 1) {
    trackRef.current?.scrollBy({ left: direction * 360, behavior: "smooth" });
  }

  return <div className="rrugc-source-ref-slider">
    <button type="button" className="rrugc-source-ref-arrow" aria-label={"Scroll " + plan.source_name + " references left"} disabled={references.length === 0} onClick={() => move(-1)}>‹</button>
    <div ref={trackRef} className="rrugc-source-ref-track" aria-label={references.length + " reference images for " + plan.source_name}>
      {references.map((reference, index) => (
        <div
          key={reference.id}
          className={
            "rrugc-source-ref-card status-" + reference.status
            + (reference.picked ? " is-picked" : "")
            + (reference.rejected ? " is-rejected" : "")
          }
        >
          <button
            type="button"
            className="rrugc-source-ref-link rrugc-source-ref-open"
            title={"Review all references · " + (reference.source_query || "Pinterest reference")}
            aria-label={"Open all references for " + plan.source_name + ", starting from reference " + (index + 1)}
            onClick={() => setReviewOpen(true)}
          >
            <img src={reference.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
            <span>{index + 1}</span>
            {reference.status === "analysis_failed" && <i className="rrugc-source-ref-ai-state is-failed">!</i>}
          </button>
          <div className="rrugc-source-ref-feedback" role="group" aria-label={"Reference " + (index + 1) + " feedback for " + plan.source_name}>
            <button
              type="button"
              className="rrugc-source-ref-vote is-good"
              aria-label={(reference.picked ? "Clear suitable mark for " : "Mark suitable ") + "reference " + (index + 1)}
              aria-pressed={reference.picked}
              disabled={!plan.campaign_id || reviewingReferenceIds.has(reference.id)}
              title={reference.picked ? "Clear suitable mark" : "Suitable / preferred training reference"}
              onClick={() => onSetReferenceFeedback(plan, reference, "good")}
            >{reviewingReferenceIds.has(reference.id) ? "…" : "✓"}</button>
            <button
              type="button"
              className="rrugc-source-ref-vote is-bad"
              aria-label={(reference.rejected ? "Clear unsuitable mark for " : "Mark unsuitable ") + "reference " + (index + 1)}
              aria-pressed={reference.rejected}
              disabled={!plan.campaign_id || reviewingReferenceIds.has(reference.id)}
              title={reference.rejected ? "Clear unsuitable mark" : "Unsuitable / remove / train negative"}
              onClick={() => onSetReferenceFeedback(plan, reference, "bad")}
            >{reviewingReferenceIds.has(reference.id) ? "…" : "×"}</button>
            <button
              type="button"
              className="rrugc-source-ref-vote is-ai"
              aria-label={"Mark AI-generated reference " + (index + 1)}
              aria-pressed={false}
              disabled={!plan.campaign_id || reviewingReferenceIds.has(reference.id)}
              title="AI-generated · remove from real refs · train AI + negative"
              onClick={() => onSetReferenceFeedback(plan, reference, "ai")}
            >{reviewingReferenceIds.has(reference.id) ? "…" : "AI"}</button>
          </div>
        </div>
      ))}
      {references.length === 0 && <div className="rrugc-source-ref-empty"><strong>No refs yet</strong><small>Auto Scout will add qualified Pinterest references here.</small></div>}
    </div>
    <button type="button" className="rrugc-source-ref-arrow" aria-label={"Scroll " + plan.source_name + " references right"} disabled={references.length === 0} onClick={() => move(1)}>›</button>
    {reviewOpen && <ReferenceReviewModal
      plan={plan}
      reviewingReferenceIds={reviewingReferenceIds}
      onSetReferenceFeedback={onSetReferenceFeedback}
      onClose={() => setReviewOpen(false)}
    />}
  </div>;
}

export function SourcePlanTable({
  plans,
  total,
  page,
  pageSize,
  query,
  sortBy = "source",
  sortDirection = "asc",
  syncing,
  reviewingReferenceIds = new Set<string>(),
  message,
  onSync,
  onPageChange,
  onPageSizeChange,
  onQueryChange,
  onSortByChange = () => undefined,
  onSortDirectionChange = () => undefined,
  onSetReferenceFeedback = () => undefined,
}: {
  plans: SourcePlan[];
  total: number;
  page: number;
  pageSize: number;
  query: string;
  sortBy?: SourcePlanSortBy;
  sortDirection?: SourcePlanSortDirection;
  syncing: boolean;
  reviewingReferenceIds?: ReadonlySet<string>;
  message: string;
  onSync: () => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onQueryChange: (query: string) => void;
  onSortByChange?: (sortBy: SourcePlanSortBy) => void;
  onSortDirectionChange?: (direction: SourcePlanSortDirection) => void;
  onSetReferenceFeedback?: (
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
    label: ReferenceManualLabel,
  ) => void;
}) {
  const pageCount = sourcePlanPageCount(total, pageSize);
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = total === 0 ? 0 : Math.min((page - 1) * pageSize + plans.length, total);
  const working = plans.filter(plan => plan.progress_count < plan.target_count && !["failed", "missing"].includes(plan.status)).length;
  const loadedRefs = plans.reduce((sum, plan) => sum + plan.reference_previews.length, 0);

  return <section id="rrugc-source-plans" className="rrugc-card rrugc-source-plans">
    <div className="rrugc-section-heading rrugc-source-plans-heading">
      <div>
        <small>DRIVE → AI CONTEXT → PINTEREST</small>
        <h2>Embroidery source → Pinterest refs</h2>
        <p>New Drive images are analyzed automatically, then sources with the same embroidery are grouped into one shared Pinterest plan and one reference pool.</p>
      </div>
      <div className="rrugc-source-plan-heading-actions">
        <span className="rrugc-source-auto-badge"><i aria-hidden="true" />Auto scan on</span>
        <span className="rrugc-source-root" title={SOURCE_ROOT_FOLDER_ID}>Drive · {SOURCE_ROOT_FOLDER_ID}</span>
        <button type="button" className="rrugc-primary" disabled={syncing} onClick={onSync}>{syncing ? "Scanning…" : "Scan now"}</button>
      </div>
    </div>

    {message && <p className="rrugc-editor-product-result" role="status">{message}</p>}

    <div className="rrugc-source-plan-kpis">
      <article><span>Embroidery groups</span><strong>{total}</strong></article>
      <article><span>This page</span><strong>{plans.length}</strong></article>
      <article><span>Working here</span><strong>{working}</strong></article>
      <article><span>Refs loaded</span><strong>{loadedRefs}</strong></article>
    </div>

    <div className="rrugc-source-plan-toolbar rrugc-source-plan-toolbar-server">
      <label className="rrugc-source-plan-search">
        <span className="sr-only">Search source plans</span>
        <input
          type="search"
          value={query}
          placeholder="Search source file or folder…"
          onChange={event => onQueryChange(event.target.value)}
        />
      </label>
      <div className="rrugc-source-plan-sort" role="group" aria-label="Sort source plans">
        <span className="rrugc-source-plan-sort-title">Sort</span>
        <label>
          <span className="sr-only">Sort source plans by</span>
          <select
            aria-label="Sort source plans by"
            value={sortBy}
            onChange={event => onSortByChange(event.target.value as SourcePlanSortBy)}
          >
            <option value="source">Source file / folder</option>
            <option value="updated">Last updated</option>
            <option value="analyzed">AI analyzed time</option>
            <option value="group_size">Embroidery group size</option>
            <option value="status">Status</option>
          </select>
        </label>
        <button
          type="button"
          className="rrugc-source-plan-sort-direction"
          aria-label={"Sort direction: " + sourcePlanSortDirectionLabel(sortBy, sortDirection)}
          onClick={() => onSortDirectionChange(sortDirection === "asc" ? "desc" : "asc")}
        >
          {sourcePlanSortDirectionLabel(sortBy, sortDirection)}
        </button>
        <span className="rrugc-source-plan-sort-count">{total} embroidery groups</span>
      </div>
    </div>

    <div className="rrugc-source-plan-table-wrap">
      <table className="rrugc-source-plan-table">
        <thead><tr><th>Source image</th><th>AI context & Pinterest plan</th><th>Scout</th><th>References</th></tr></thead>
        <tbody>
          {plans.map(plan => {
            const progress = sourcePlanProgressPercent(plan);
            const context = sourcePlanContextSummary(plan);
            const themes = plan.visual_context?.themes?.slice(0, 3) || [];
            const referenceContexts = plan.reference_contexts || [];
            const pickedCount = plan.reference_previews.filter(reference => reference.picked).length;
            const rejectedCount = plan.reference_previews.filter(reference => reference.rejected).length;
            const scoutStatus = scoutStatusPresentation(plan);
            return <tr key={plan.id}>
              <td className="rrugc-source-cell"><div className="rrugc-source-file rrugc-source-file-grouped">
                <SourceImageGroup plan={plan} />
                <span>
                  <strong title={plan.source_name}>{plan.source_name}</strong>
                  <small>{plan.embroidery_group_size} source {plan.embroidery_group_size === 1 ? "image" : "images"}{plan.embroidery_group_size > 1 ? " · same embroidery" : ""}</small>
                  <em>{sourceMeta(plan) || "Image source"}</em>
                </span>
              </div></td>
              <td className="rrugc-source-plan-context">
                <div className="rrugc-source-context-head"><span className={"rrugc-source-plan-status tone-" + planTone(plan)}>{planStatusLabel(plan)}</span>{plan.analyzed_at && <small>Analyzed {new Date(plan.analyzed_at).toLocaleString()}</small>}</div>
                <p title={context}>{context}</p>
                {(plan.embroidery_group_size > 1 || themes.length > 0 || referenceContexts.length > 0) && <div className="rrugc-source-theme-chips">{plan.embroidery_group_size > 1 && <span>Same embroidery · {plan.embroidery_group_size} images</span>}{referenceContexts.includes("hand_holding_hat") && <span>Hand holding hat</span>}{themes.map(theme => <span key={theme}>{theme}</span>)}</div>}
                <div className="rrugc-source-query-chips">{plan.search_queries.slice(0, 4).map(keyword => <span key={keyword} title={keyword}>{keyword}</span>)}{plan.search_queries.length > 4 && <span>+{plan.search_queries.length - 4}</span>}{plan.search_queries.length === 0 && <small>{plan.status === "ready" ? "No search query" : "Waiting for AI search plan…"}</small>}</div>
                {plan.last_error_code && <small className="rrugc-source-error">{plan.last_error_code}</small>}
              </td>
              <td className="rrugc-source-scout-cell">
                <div className="rrugc-source-progress-copy"><strong>{plan.progress_count}<small>/{plan.target_count}</small></strong><span>{progress}%</span></div>
                <span className="rrugc-progress rrugc-source-progress"><i style={{ width: progress + "%" }} /></span>
                <div className="rrugc-source-scout-meta"><span className={"rrugc-agent status-" + scoutStatus.tone}>{scoutStatus.label}</span><small>{plan.pipeline_count} in pipeline · {plan.candidate_count} found</small></div>
              </td>
              <td className="rrugc-source-refs-cell">
                <div className="rrugc-source-refs-head"><strong>{plan.approved_count} refs · {plan.pending_ai_count} pending AI · {pickedCount} ✓ · {rejectedCount} ×</strong><small>Pending AI = waiting analysis · ✓ positive · × negative/remove · AI synthetic/remove + train AI · {plan.drive_ready_count} usable Drive ready</small></div>
                <ReferenceSlider plan={plan} reviewingReferenceIds={reviewingReferenceIds} onSetReferenceFeedback={onSetReferenceFeedback} />
              </td>
            </tr>;
          })}
          {plans.length === 0 && <tr><td colSpan={4} className="rrugc-source-plan-empty">{query.trim() ? "No embroidery group matches this search." : "No source images yet. Auto scan will create embroidery groups when images appear in the configured Drive folders."}</td></tr>}
        </tbody>
      </table>
    </div>

    <div className="rrugc-source-pagination">
      <span>{start}–{end} of {total}</span>
      <div className="rrugc-source-page-controls">
        <button type="button" disabled={page <= 1} onClick={() => onPageChange(1)} aria-label="First page">«</button>
        <button type="button" disabled={page <= 1} onClick={() => onPageChange(Math.max(1, page - 1))} aria-label="Previous page">‹</button>
        <strong>Page {page} / {pageCount}</strong>
        <button type="button" disabled={page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, page + 1))} aria-label="Next page">›</button>
        <button type="button" disabled={page >= pageCount} onClick={() => onPageChange(pageCount)} aria-label="Last page">»</button>
      </div>
      <label>Rows<select value={pageSize} onChange={event => onPageSizeChange(Number(event.target.value))}>{PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    </div>
  </section>;
}
