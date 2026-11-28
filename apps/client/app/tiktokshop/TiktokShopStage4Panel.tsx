import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { RrugcStageHeader } from "../realistic-review-ugc/RrugcStageHeader";
import { RrugcActionIcon } from "../realistic-review-ugc/RrugcActionIcon";
import { RrugcSmartSearchInput } from "../realistic-review-ugc/RrugcSmartSearchInput";
import { stage2JobOutputThumbnailUrl, stage2JobOutputUrl, type SourcePlanSortBy, type SourcePlanSortDirection } from "../realistic-review-ugc/api";
import type { SourcePlan, Stage2Job } from "../realistic-review-ugc/types";

type Props = {
  plans: SourcePlan[];
  jobs: Stage2Job[];
  loading: boolean;
  busy: boolean;
  page: number;
  total: number;
  pageSize: number;
  query: string;
  folderPath: string | null;
  sortBy: SourcePlanSortBy;
  sortDirection: SourcePlanSortDirection;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onQueryChange: (query: string) => void;
  onClearFolder: () => void;
  onSortByChange: (sortBy: SourcePlanSortBy) => void;
  onSortDirectionChange: (direction: SourcePlanSortDirection) => void;
  onCreate: (plan: SourcePlan, referenceIds: string[]) => Promise<void>;
  onRetry: (job: Stage2Job) => Promise<void>;
  onRegenerate: (job: Stage2Job) => Promise<void>;
  onRefresh: () => void;
};

const TARGET_SKILL = "tiktokshop-apparel-ugc-v1";
const jobOrder = (a: Stage2Job, b: Stage2Job) => b.created_at.localeCompare(a.created_at);

/** Rails scroll independently of the table; buttons disappear at the edges. */
function MediaRail({ children, label }: { children: ReactNode; label: string }) {
  const track = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ prev: false, next: false });
  useEffect(() => {
    const el = track.current;
    if (!el) return;
    const sync = () => {
      const prev = el.scrollLeft > 1;
      const next = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
      setEdges(old => old.prev === prev && old.next === next ? old : { prev, next });
    };
    sync();
    el.addEventListener("scroll", sync, { passive: true });
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(sync) : null;
    observer?.observe(el);
    if (el.firstElementChild) observer?.observe(el.firstElementChild);
    return () => { observer?.disconnect(); el.removeEventListener("scroll", sync); };
  }, [children]);
  const move = (direction: number) => {
    const el = track.current;
    if (el) el.scrollBy({ left: direction * Math.max(80, el.clientWidth * .8), behavior: "smooth" });
  };
  return <div className="tiktok-media-rail">
    <div ref={track} className="tiktok-media-track" role="region" aria-label={label} tabIndex={0}>
      <div className="tiktok-media-items">{children}</div>
    </div>
    {edges.prev && <button type="button" className="tiktok-rail-arrow is-prev" aria-label={"Previous " + label} onClick={() => move(-1)}><RrugcActionIcon name="chevron-left" /></button>}
    {edges.next && <button type="button" className="tiktok-rail-arrow is-next" aria-label={"Next " + label} onClick={() => move(1)}><RrugcActionIcon name="chevron-right" /></button>}
  </div>;
}

export function TiktokShopStage4Panel({
  plans, jobs, loading, busy, page, total, pageSize, query, folderPath, sortBy, sortDirection,
  onPageChange, onPageSizeChange, onQueryChange, onClearFolder, onSortByChange, onSortDirectionChange,
  onCreate, onRetry, onRegenerate, onRefresh,
}: Props) {
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const byPlan = useMemo(() => {
    const lookup = new Map<string, Stage2Job[]>();
    for (const plan of plans) {
      const ids = new Set([plan.id, ...(plan.source_group_images || []).map(row => row.id)]);
      lookup.set(plan.id, jobs.filter(job => ids.has(job.source_plan_id)).sort(jobOrder));
    }
    return lookup;
  }, [plans, jobs]);
  const jobCounts = useMemo(() => ({
    active: jobs.filter(job => job.status === "queued" || job.status === "running").length,
    completed: jobs.filter(job => job.status === "completed").length,
    failed: jobs.filter(job => job.status === "failed").length,
  }), [jobs]);
  const suggestions = useMemo(() => plans.map(plan => ({
    value: plan.source_name, meta: plan.source_relative_path, badge: "Source",
  })), [plans]);
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const isBusy = busy || busyPlan !== null || busyJob !== null;

  function toggle(planId: string, referenceId: string) {
    setPicked(current => {
      const selected = current[planId] || [];
      return { ...current, [planId]: selected.includes(referenceId)
        ? selected.filter(id => id !== referenceId)
        : selected.length < 3 ? [...selected, referenceId] : selected };
    });
  }
  async function generate(plan: SourcePlan, ids: string[]) {
    setBusyPlan(plan.id);
    try {
      await onCreate(plan, ids);
      setPicked(current => ({ ...current, [plan.id]: [] }));
    } catch {
      // Parent reports API errors in the workspace alert.
    } finally { setBusyPlan(null); }
  }
  async function runJobAction(job: Stage2Job, action: "retry" | "regenerate") {
    setBusyJob(job.id);
    try { await (action === "retry" ? onRetry(job) : onRegenerate(job)); }
    catch {
      // Parent reports API errors in the workspace alert.
    } finally { setBusyJob(null); }
  }

  return <section className="tiktok-generation-workspace" aria-label="TikTokShop Stage 4 isolated apparel generation">
    <RrugcStageHeader kicker="STAGE 4 · APPAREL IMAGE EDIT"
      title="Image Generation" description={<span title={TARGET_SKILL}>TikTokShop apparel Skill · internal drafts</span>}
      actions={<button type="button" className="rrugc-icon-action tiktok-control" onClick={onRefresh}
        disabled={loading || isBusy} title={"Refresh jobs · " + TARGET_SKILL}><RrugcActionIcon name="refresh" />Refresh jobs</button>} />
    <div className="tiktok-kpi-strip" aria-label="Stage 4 workspace job summary">
      <article><span>Product groups</span><strong>{total}</strong></article>
      <article><span>Active jobs</span><strong>{jobCounts.active}</strong></article>
      <article><span>Completed</span><strong>{jobCounts.completed}</strong></article>
      <article><span>Failed</span><strong>{jobCounts.failed}</strong></article>
    </div>
    <div className="tiktok-generation-toolbar">
      <RrugcSmartSearchInput stageId="tiktokshop-stage4" query={query} onQueryChange={onQueryChange}
        suggestions={suggestions} placeholder="Search source file or folder…" label="Search Stage 4 product groups" />
      <div className="tiktok-generation-filters">
        {folderPath && <button className="tiktok-folder-filter tiktok-control" type="button" title={folderPath} onClick={onClearFolder}>
          <RrugcActionIcon name="folder" /><span>{folderPath.split("/").pop()}</span> ×
        </button>}
        <select aria-label="Sort Stage 4 product groups" value={sortBy} onChange={event => onSortByChange(event.target.value as SourcePlanSortBy)}>
          <option value="source">Source file / folder</option><option value="updated">Last updated</option>
          <option value="analyzed">AI analyzed time</option><option value="group_size">Embroidery group size</option>
          <option value="status">Status</option><option value="progress">Scout progress</option>
          <option value="refs">Drive-ready references</option><option value="active_jobs">Active jobs</option><option value="outputs">Completed outputs</option>
        </select>
        <button type="button" className="tiktok-control" aria-label="Sort Stage 4 direction" onClick={() => onSortDirectionChange(sortDirection === "asc" ? "desc" : "asc")}>
          <RrugcActionIcon name="sort" />{sortDirection === "asc" ? "Ascending" : "Descending"}
        </button>
      </div>
    </div>
    <div className="tiktok-generation-table-wrap" aria-busy={loading}>
      <table className="tiktok-generation-table">
        <colgroup><col className="tiktok-col-source" /><col className="tiktok-col-refs" /><col className="tiktok-col-outputs" /><col className="tiktok-col-actions" /></colgroup>
        <thead><tr><th>Source / Product</th><th>Drive-ready references</th><th>Output history</th><th>Action</th></tr></thead>
        <tbody>
          {loading && plans.length === 0 && <tr><td colSpan={4} className="tiktok-empty">Loading TikTokShop product sources and generation history…</td></tr>}
          {!loading && plans.length === 0 && <tr><td colSpan={4} className="tiktok-empty">No matching sources. Run Stage 3 Drive Scan or change filters.</td></tr>}
          {plans.map(plan => {
            const planJobs = byPlan.get(plan.id) || [];
            const doneIds = new Set(planJobs.filter(job => job.status === "completed").flatMap(job => job.selected_candidate_ids));
            const references = plan.reference_previews.filter(ref => ref.status === "drive_ready" && !ref.rejected);
            const selected = (picked[plan.id] || []).filter(id => references.some(ref => ref.id === id) && !doneIds.has(id));
            const canRun = plan.status === "ready" && Boolean(plan.campaign_id) && selected.length > 0 && !isBusy && !loading;
            return <tr key={plan.id} className="tiktok-generation-row">
              <td data-label="Source / Product">
                <div className="tiktok-product-cell">
                  <img src={plan.source_preview_url} alt={plan.source_name} width={76} height={76} loading="lazy" decoding="async" />
                  <div className="tiktok-product-copy">
                    <strong title={plan.source_relative_path}>{plan.source_name}</strong>
                    <small title={plan.source_relative_path}>{plan.source_relative_path}</small>
                    <span className={"tiktok-status status-" + plan.status}>{plan.status}</span>
                  </div>
                </div>
              </td>
              <td data-label="Drive-ready references">
                <div className="tiktok-cell-heading"><span>{references.length} refs</span><strong>{selected.length}/3 selected</strong>{references.length > 24 && <small>24 shown</small>}</div>
                {references.length === 0 ? <span className="tiktok-cell-empty">No Drive-ready refs · Stage 3</span> :
                  <MediaRail label={"references for " + plan.source_name}>
                    {references.slice(0, 24).map(ref => {
                      const generated = doneIds.has(ref.id);
                      const checked = selected.includes(ref.id);
                      return <label key={ref.id} className={"tiktok-reference-tile" + (checked ? " is-selected" : "") + (generated ? " is-generated" : "")}
                        title={generated ? "Already generated from this reference" : ref.source_query || ref.pin_url}>
                        <img src={ref.image_url} alt="Pinterest scene reference" loading="lazy" decoding="async" />
                        <input type="checkbox" checked={checked} disabled={generated || isBusy || (!checked && selected.length >= 3)}
                          aria-label={"Pick reference " + ref.id} onChange={() => toggle(plan.id, ref.id)} />
                        {generated && <span className="tiktok-ref-done" aria-label="Already generated">✓</span>}
                      </label>;
                    })}
                  </MediaRail>}
              </td>
              <td data-label="Output history">
                <div className="tiktok-cell-heading"><span>{planJobs.length} runs</span><small>{planJobs.filter(job => job.status === "completed").length} completed</small>{planJobs.length > 12 && <small>12 shown</small>}</div>
                {planJobs.length === 0 ? <span className="tiktok-cell-empty">No generation jobs yet</span> :
                  <MediaRail label={"outputs for " + plan.source_name}>
                    {planJobs.slice(0, 12).map(job => <div key={job.id} className="tiktok-output-tile">
                      {job.status === "completed" && job.output_remote_file_id
                        ? <a className="tiktok-output-media" href={stage2JobOutputUrl(job.id, "tiktokshop")} target="_blank" rel="noreferrer" title="Open completed TikTokShop image">
                          <img src={stage2JobOutputThumbnailUrl(job.id, 256, job.output_remote_file_id, "tiktokshop")} alt="Generated apparel UGC draft" loading="lazy" decoding="async" />
                        </a> : <div className="tiktok-output-media tiktok-output-placeholder">{job.status === "running" ? "Generating" : job.status}</div>}
                      <span className={"tiktok-output-status status-" + job.status} title={[job.last_error_code, job.last_error_message].filter(Boolean).join(" · ")}>{job.status}</span>
                      {job.status === "failed" && <button type="button" className="tiktok-output-action" disabled={isBusy} onClick={() => void runJobAction(job, "retry")} title="Retry failed job">Retry</button>}
                      {job.status === "completed" && <button type="button" className="tiktok-output-action" disabled={isBusy} onClick={() => void runJobAction(job, "regenerate")} title="Regenerate draft">Regenerate</button>}
                    </div>)}
                  </MediaRail>}
              </td>
              <td data-label="Action">
                <button type="button" className="rrugc-primary tiktok-generate-button" disabled={!canRun} title="Generate apparel UGC draft" onClick={() => void generate(plan, selected)}>
                  <RrugcActionIcon name="sparkles" />{busyPlan === plan.id ? "Queuing…" : "Generate"}</button>
                <small className="tiktok-action-hint">{selected.length ? selected.length + " reference" + (selected.length > 1 ? "s" : "") : "Select 1–3 refs"}</small>
              </td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>
    <div className="tiktok-pagination">
      <span>{total ? (page - 1) * pageSize + 1 : 0}–{Math.min((page - 1) * pageSize + plans.length, total)} / {total}</span>
      <div>
        <button type="button" className="tiktok-control" aria-label="Previous Stage 4 page" disabled={loading || page <= 1} onClick={() => onPageChange(page - 1)}><RrugcActionIcon name="chevron-left" /></button>
        <strong>{page} / {pageCount}</strong>
        <button type="button" className="tiktok-control" aria-label="Next Stage 4 page" disabled={loading || page >= pageCount} onClick={() => onPageChange(page + 1)}><RrugcActionIcon name="chevron-right" /></button>
      </div>
      <label>Rows <select aria-label="Stage 4 rows per page" value={pageSize} disabled={loading} onChange={event => onPageSizeChange(Number(event.target.value))}>{[10,20,50].map(size => <option key={size}>{size}</option>)}</select></label>
    </div>
  </section>;
}
