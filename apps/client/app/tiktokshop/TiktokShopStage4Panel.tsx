import { useMemo, useState } from "react";
import { RrugcStageHeader } from "../realistic-review-ugc/RrugcStageHeader";
import { stage2JobOutputThumbnailUrl, stage2JobOutputUrl } from "../realistic-review-ugc/api";
import type { SourcePlan, Stage2Job } from "../realistic-review-ugc/types";

type Props = {
  plans: SourcePlan[];
  jobs: Stage2Job[];
  loading: boolean;
  busy: boolean;
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onCreate: (plan: SourcePlan, referenceIds: string[]) => Promise<void>;
  onRetry: (job: Stage2Job) => Promise<void>;
  onRegenerate: (job: Stage2Job) => Promise<void>;
  onRefresh: () => void;
};

const TARGET_SKILL = "tiktokshop-apparel-ugc-v1";
const jobOrder = (a: Stage2Job, b: Stage2Job) => b.created_at.localeCompare(a.created_at);

export function TiktokShopStage4Panel({
  plans, jobs, loading, busy, page, total, pageSize,
  onPageChange, onCreate, onRetry, onRegenerate, onRefresh,
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

  return <section className="rrugc-stage-panel" aria-label="TikTokShop Stage 4 isolated apparel generation">
    <RrugcStageHeader
      kicker="STAGE 4 · PINTEREST → APPAREL IMAGE EDIT"
      title="Image Generation"
      description="Same CAM Stage 4 pipeline with a TikTokShop-only apparel Skill, reference selection, output history and retry."
    />
    <div className="rrugc-card" style={{ marginBottom: 14, padding: 14, display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
      <div>
        <strong>Skill · {TARGET_SKILL}</strong>
        <p style={{ margin: "4px 0 0" }}>Draft images only. Original product artwork and TikTokShop Drive location are authoritative. This never uses a GatorHats cap Skill.</p>
      </div>
      <button type="button" className="rrugc-icon-action" onClick={onRefresh} disabled={loading || busy}>Refresh jobs</button>
    </div>
    {loading && <p>Loading TikTokShop product sources and generation history…</p>}
    {!loading && plans.length === 0 && <div className="rrugc-card" style={{ padding: 18 }}>
      No TikTokShop sources match this page yet. Run Stage 3 Drive Scan first.
    </div>}
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 370px), 1fr))", alignItems: "start", gap: 14 }}>
      {plans.map(plan => {
        const planJobs = byPlan.get(plan.id) || [];
        const doneIds = new Set(planJobs.filter(job => job.status === "completed").flatMap(job => job.selected_candidate_ids));
        const references = plan.reference_previews.filter(ref => ref.status === "drive_ready" && !ref.rejected);
        const selected = (picked[plan.id] || []).filter(id => references.some(ref => ref.id === id) && !doneIds.has(id));
        const canRun = plan.status === "ready" && Boolean(plan.campaign_id) && selected.length > 0 && !busy && busyPlan !== plan.id;
        return <article className="rrugc-card" key={plan.id} style={{ padding: 14 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
            <img src={plan.source_preview_url} alt={plan.source_name} width={84} height={84}
              loading="lazy" decoding="async" style={{ width: 84, height: 84, objectFit: "contain", borderRadius: 8, background: "var(--rrugc-surface-raised, #f2f4f8)" }}/>
            <div style={{ minWidth: 0 }}>
              <strong title={plan.source_relative_path} style={{ overflowWrap: "anywhere" }}>{plan.source_name}</strong>
              <p style={{ margin: "4px 0", fontSize: 12, overflowWrap: "anywhere" }}>{plan.source_relative_path}</p>
              <small>{references.length} imported refs · {planJobs.length} runs · {plan.status}</small>
            </div>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", marginBottom: 8 }}>
            <strong>Pick 1–3 lifestyle references</strong>
            <small>{selected.length}/3 picked</small>
          </div>
          {references.length === 0
            ? <p style={{ fontSize: 13 }}>No Drive-ready Pinterest references yet. Start the paired TikTokShop Scout in Stage 3.</p>
            : <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 7 }}>
              {references.slice(0, 24).map(ref => {
                const generated = doneIds.has(ref.id);
                const checked = selected.includes(ref.id);
                return <label key={ref.id} title={generated ? "Already generated from this reference" : ref.source_query || ref.pin_url}
                  style={{ position: "relative", display: "block", opacity: generated ? .45 : 1, cursor: generated ? "not-allowed" : "pointer" }}>
                  <img src={ref.image_url} alt="Pinterest scene reference" loading="lazy" decoding="async"
                    style={{ width: "100%", height: 74, objectFit: "cover", borderRadius: 7, border: checked ? "2px solid #3868db" : "1px solid #dce2ef" }}/>
                  <input type="checkbox" checked={checked} disabled={generated || busy || Boolean(busyPlan)}
                    aria-label={"Pick reference " + ref.id}
                    onChange={() => toggle(plan.id, ref.id)}
                    style={{ position: "absolute", top: 4, left: 4, accentColor: "#3868db" }}/>
                </label>;
              })}
            </div>}
          <button type="button" className="rrugc-primary" disabled={!canRun}
            onClick={() => void generate(plan, selected)}
            style={{ marginTop: 12, width: "100%" }}>
            {busyPlan === plan.id ? "Queuing…" : "Generate apparel UGC draft"}
          </button>
          <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--rrugc-border, #e0e5ee)" }}>
            <strong>Output history</strong>
            {planJobs.length === 0 && <p style={{ fontSize: 12 }}>No image-generation jobs for this product.</p>}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8, marginTop: 8 }}>
              {planJobs.slice(0, 12).map(job => <div key={job.id} style={{ minWidth: 0 }}>
                {job.status === "completed" && job.output_remote_file_id
                  ? <a href={stage2JobOutputUrl(job.id, "tiktokshop")} target="_blank" rel="noreferrer"
                      title="Open completed TikTokShop image">
                    <img src={stage2JobOutputThumbnailUrl(job.id, 256, job.output_remote_file_id, "tiktokshop")}
                      alt="Generated apparel UGC draft" loading="lazy" decoding="async"
                      style={{ width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: 7 }}/>
                  </a>
                  : <div style={{ aspectRatio: "1", display: "grid", placeItems: "center", border: "1px dashed #a8b4c8", borderRadius: 7, fontSize: 12 }}>
                      {job.status === "running" ? "Generating" : job.status}
                    </div>}
                <div title={[job.last_error_code, job.last_error_message].filter(Boolean).join(" · ")}
                  style={{ fontSize: 11, marginTop: 3, textTransform: "capitalize" }}>{job.status}</div>
                {job.status === "failed" && <button type="button" className="rrugc-icon-action"
                  disabled={busy || busyJob === job.id} onClick={() => void runJobAction(job, "retry")}>Retry</button>}
                {job.status === "completed" && <button type="button" className="rrugc-icon-action"
                  disabled={busy || busyJob === job.id} onClick={() => void runJobAction(job, "regenerate")}>Regenerate</button>}
              </div>)}
            </div>
          </div>
        </article>;
      })}
    </div>
    <div style={{ display: "flex", alignItems: "center", gap: 12, justifyContent: "flex-end", marginTop: 16 }}>
      <span>{total} product groups</span>
      <button type="button" className="rrugc-icon-action" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>Previous</button>
      <span>Page {page} / {Math.max(1, Math.ceil(total / pageSize))}</span>
      <button type="button" className="rrugc-icon-action" disabled={page * pageSize >= total} onClick={() => onPageChange(page + 1)}>Next</button>
    </div>
  </section>;
}
