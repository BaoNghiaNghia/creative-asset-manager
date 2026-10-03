import { useEffect, useMemo, useState } from "react";
import { stage2JobOutputUrl } from "./api";
import type { SourcePlan, SourcePlanReferencePreview, Stage2Job } from "./types";

const MAX_REFS = 10;
const SKILL_NAME = "gatorhats-8869-image-studio";

function eligibleReference(reference: SourcePlanReferencePreview) {
  return reference.status === "drive_ready" && !reference.rejected;
}

function latestJobsByPlan(jobs: Stage2Job[]) {
  const result = new Map<string, Stage2Job>();
  for (const job of jobs) {
    if (!result.has(job.source_plan_id)) result.set(job.source_plan_id, job);
  }
  return result;
}

function jobLabel(job: Stage2Job | undefined) {
  if (!job) return "Not queued";
  if (job.status === "queued") return "Queued";
  if (job.status === "running") return "Generating";
  if (job.status === "completed") return "Completed";
  return "Failed";
}

export function Stage2JobTable({
  plans,
  jobs,
  creatingPlanIds,
  message,
  onCreateJob,
}: {
  plans: SourcePlan[];
  jobs: Stage2Job[];
  creatingPlanIds: ReadonlySet<string>;
  message?: string;
  onCreateJob: (plan: SourcePlan, candidateIds: string[]) => void;
}) {
  const [selectedByPlan, setSelectedByPlan] = useState<Record<string, string[]>>({});
  const latest = useMemo(() => latestJobsByPlan(jobs), [jobs]);
  const stage2Plans = useMemo(
    () => plans.filter(plan => (
      plan.status === "ready"
      && Boolean(plan.campaign_id)
      && Boolean(
        plan.embroidery_signature
        || plan.visual_context?.embroidery_identity
        || plan.visual_context?.embroidery_text?.length
      )
    )),
    [plans],
  );

  useEffect(() => {
    setSelectedByPlan(current => {
      let changed = false;
      const next = { ...current };
      for (const plan of stage2Plans) {
        if (Object.prototype.hasOwnProperty.call(next, plan.id)) continue;
        next[plan.id] = plan.reference_previews
          .filter(reference => reference.picked && eligibleReference(reference))
          .slice(0, MAX_REFS)
          .map(reference => reference.id);
        changed = true;
      }
      return changed ? next : current;
    });
  }, [stage2Plans]);

  function toggleReference(planId: string, referenceId: string) {
    setSelectedByPlan(current => {
      const selected = current[planId] || [];
      if (selected.includes(referenceId)) {
        return {
          ...current,
          [planId]: selected.filter(value => value !== referenceId),
        };
      }
      if (selected.length >= MAX_REFS) return current;
      return { ...current, [planId]: [...selected, referenceId] };
    });
  }

  return <section className="rrugc-card rrugc-stage2">
    <div className="rrugc-section-heading rrugc-stage2-heading">
      <div>
        <small>STAGE 2 · EMBROIDERY → SELECT REFS → SKILL</small>
        <h2>Generation jobs</h2>
        <p>Pick up to 10 Drive-ready Pinterest references for each embroidered hat, then generate one master image with the GatorHats Image Studio skill.</p>
      </div>
      <span className="rrugc-source-auto-badge"><i aria-hidden="true" />Max {MAX_REFS} refs / job</span>
    </div>

    {message && <p className="rrugc-editor-product-result" role="status">{message}</p>}

    <div className="rrugc-source-plan-table-wrap">
      <table className="rrugc-source-plan-table rrugc-stage2-table">
        <thead>
          <tr>
            <th>Embroidery source</th>
            <th>Pinterest refs · pick up to 10</th>
            <th>Skill</th>
            <th>Job / output</th>
          </tr>
        </thead>
        <tbody>
          {stage2Plans.map(plan => {
            const available = plan.reference_previews.filter(eligibleReference);
            const selected = selectedByPlan[plan.id] || [];
            const job = latest.get(plan.id);
            const busy = creatingPlanIds.has(plan.id) || job?.status === "queued" || job?.status === "running";
            return <tr key={plan.id}>
              <td className="rrugc-source-cell">
                <div className="rrugc-source-file">
                  <img src={plan.source_preview_url} alt="" loading="lazy" />
                  <span>
                    <strong title={plan.source_name}>{plan.source_name}</strong>
                    <small>{plan.embroidery_group_size} source {plan.embroidery_group_size === 1 ? "image" : "images"}</small>
                    <em>{plan.visual_context?.embroidery_text?.join(" · ") || plan.visual_context?.embroidery_identity || "Embroidery detected"}</em>
                  </span>
                </div>
              </td>
              <td>
                <div className="rrugc-stage2-ref-head">
                  <strong>{selected.length}/{MAX_REFS} selected</strong>
                  <small>{available.length} Drive-ready refs available</small>
                </div>
                <div className="rrugc-stage2-ref-grid">
                  {available.slice(0, 40).map(reference => {
                    const checked = selected.includes(reference.id);
                    const atLimit = selected.length >= MAX_REFS && !checked;
                    return <button
                      type="button"
                      key={reference.id}
                      className={"rrugc-stage2-ref " + (checked ? "is-selected" : "")}
                      aria-pressed={checked}
                      disabled={atLimit || busy}
                      title={checked ? "Remove reference" : atLimit ? "Maximum 10 references" : "Use this Pinterest reference"}
                      onClick={() => toggleReference(plan.id, reference.id)}
                    >
                      <img src={reference.image_url} alt="" loading="lazy" />
                      <span>{checked ? "✓" : "+"}</span>
                    </button>;
                  })}
                  {available.length === 0 && <small className="rrugc-stage2-empty-ref">Waiting for approved Pinterest references to finish Drive import.</small>}
                </div>
              </td>
              <td className="rrugc-stage2-skill">
                <strong>{"$" + SKILL_NAME}</strong>
                <small>Master-first · source embroidery locked · Pinterest refs = context/style only</small>
                <button
                  type="button"
                  className="rrugc-primary"
                  disabled={busy || selected.length === 0}
                  onClick={() => onCreateJob(plan, selected)}
                >
                  {creatingPlanIds.has(plan.id) ? "Queuing…" : busy ? "Generating…" : "Generate master"}
                </button>
              </td>
              <td className="rrugc-stage2-status">
                <span className={"rrugc-source-plan-status tone-" + (
                  job?.status === "completed" ? "positive"
                    : job?.status === "failed" ? "negative"
                      : job ? "working" : "muted"
                )}>{jobLabel(job)}</span>
                {job && <small>{job.reference_count} refs · {new Date(job.created_at).toLocaleString()}</small>}
                {job?.last_error_code && <small className="rrugc-source-error">{job.last_error_code}</small>}
                {job?.status === "completed" && <a
                  className="rrugc-stage2-output"
                  href={stage2JobOutputUrl(job.id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img src={stage2JobOutputUrl(job.id)} alt="Generated Stage 2 master" loading="lazy" />
                  <span>Open master ↗</span>
                </a>}
              </td>
            </tr>;
          })}
          {stage2Plans.length === 0 && <tr><td colSpan={4} className="rrugc-source-plan-empty">Stage 2 jobs will appear here after Stage 1 finishes embroidery context analysis.</td></tr>}
        </tbody>
      </table>
    </div>
  </section>;
}
