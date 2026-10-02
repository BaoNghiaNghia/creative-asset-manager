import { useMemo, useState } from "react";
import type { SourcePlan } from "./types";

const SOURCE_ROOT_FOLDER_ID = "1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX";
const INITIAL_VISIBLE_ROWS = 40;

export function sourcePlanProgressPercent(plan: Pick<SourcePlan, "progress_count" | "target_count">): number {
  if (plan.target_count <= 0) return 0;
  return Math.min(100, Math.round((plan.progress_count / plan.target_count) * 100));
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

function sourceMeta(plan: SourcePlan): string {
  const dimensions = plan.source_width && plan.source_height
    ? plan.source_width + "×" + plan.source_height
    : "";
  const size = plan.source_size_bytes
    ? (plan.source_size_bytes / (1024 * 1024)).toFixed(plan.source_size_bytes > 10 * 1024 * 1024 ? 0 : 1) + " MB"
    : "";
  return [dimensions, size].filter(Boolean).join(" · ");
}

function ReferenceSlots({ plan }: { plan: SourcePlan }) {
  const slotCount = Math.max(1, Math.min(20, plan.target_count || 20));
  const slots = Array.from({ length: slotCount }, (_, index) => plan.reference_previews[index] || null);
  return <div className="rrugc-source-ref-grid" aria-label={plan.progress_count + " of " + plan.target_count + " references ready"}>
    {slots.map((reference, index) => reference ? (
      <a
        key={reference.id}
        href={reference.pin_url}
        target="_blank"
        rel="noreferrer"
        className={"rrugc-source-ref-tile status-" + reference.status}
        title={(reference.source_query || "Pinterest reference") + " · " + reference.status.replaceAll("_", " ")}
      >
        <img src={reference.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
        <span>{index + 1}</span>
      </a>
    ) : (
      <span key={"empty-" + index} className="rrugc-source-ref-tile is-empty" aria-hidden="true">
        <small>{index + 1}</small>
      </span>
    ))}
  </div>;
}

export function SourcePlanTable({
  plans,
  syncing,
  message,
  onSync,
  onOpenCampaign,
}: {
  plans: SourcePlan[];
  syncing: boolean;
  message: string;
  onSync: () => void;
  onOpenCampaign: (campaignId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "working" | "complete" | "attention">("all");
  const [visibleRows, setVisibleRows] = useState(INITIAL_VISIBLE_ROWS);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return plans.filter(plan => {
      if (needle && ![
        plan.source_name,
        plan.source_relative_path,
        plan.campaign_name,
        ...plan.search_queries,
        ...(plan.visual_context?.themes || []),
        ...(plan.visual_context?.scene_hints || []),
      ].some(value => value?.toLocaleLowerCase().includes(needle))) return false;
      if (statusFilter === "complete") return plan.target_count > 0 && plan.progress_count >= plan.target_count;
      if (statusFilter === "attention") return ["failed", "retry", "missing"].includes(plan.status);
      if (statusFilter === "working") {
        return plan.progress_count < plan.target_count && !["failed", "missing"].includes(plan.status);
      }
      return true;
    });
  }, [plans, query, statusFilter]);

  const shown = filtered.slice(0, visibleRows);
  const complete = plans.filter(plan => plan.target_count > 0 && plan.progress_count >= plan.target_count).length;
  const working = plans.filter(plan => plan.progress_count < plan.target_count && !["failed", "missing"].includes(plan.status)).length;
  const readyRefs = plans.reduce((sum, plan) => sum + plan.progress_count, 0);
  const targetRefs = plans.reduce((sum, plan) => sum + plan.target_count, 0);

  return <section id="rrugc-source-plans" className="rrugc-card rrugc-source-plans">
    <div className="rrugc-section-heading rrugc-source-plans-heading">
      <div>
        <small>DRIVE → AI CONTEXT → PINTEREST</small>
        <h2>Embroidery source → Pinterest refs</h2>
        <p>Each image inside child folders becomes one independent plan that scouts exactly 20 context-matched references.</p>
      </div>
      <div className="rrugc-source-plan-heading-actions">
        <span className="rrugc-source-root" title={SOURCE_ROOT_FOLDER_ID}>Drive · {SOURCE_ROOT_FOLDER_ID}</span>
        <button
          type="button"
          className="rrugc-primary"
          disabled={syncing}
          onClick={onSync}
        >
          {syncing ? "Scanning source…" : "Sync source folder"}
        </button>
      </div>
    </div>

    {message && <p className="rrugc-editor-product-result" role="status">{message}</p>}

    <div className="rrugc-source-plan-kpis">
      <article><span>Source images</span><strong>{plans.length}</strong></article>
      <article><span>Working</span><strong>{working}</strong></article>
      <article><span>Complete</span><strong>{complete}</strong></article>
      <article><span>Refs ready</span><strong>{readyRefs}<small>/{targetRefs}</small></strong></article>
    </div>

    <div className="rrugc-source-plan-toolbar">
      <label>
        <span className="sr-only">Search source plans</span>
        <input
          type="search"
          value={query}
          placeholder="Search file, folder, context, or keyword…"
          onChange={event => {
            setQuery(event.target.value);
            setVisibleRows(INITIAL_VISIBLE_ROWS);
          }}
        />
      </label>
      <div className="rrugc-source-plan-filters" role="group" aria-label="Source plan status">
        {([
          ["all", "All"],
          ["working", "Working"],
          ["complete", "Complete"],
          ["attention", "Needs attention"],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={statusFilter === value ? "active" : ""}
            onClick={() => {
              setStatusFilter(value);
              setVisibleRows(INITIAL_VISIBLE_ROWS);
            }}
          >{label}</button>
        ))}
      </div>
      <span>{filtered.length} rows</span>
    </div>

    <div className="rrugc-source-plan-table-wrap">
      <table className="rrugc-source-plan-table">
        <thead>
          <tr>
            <th>Source image</th>
            <th>AI context & Pinterest plan</th>
            <th>Scout</th>
            <th>References</th>
          </tr>
        </thead>
        <tbody>
          {shown.map(plan => {
            const progress = sourcePlanProgressPercent(plan);
            const context = sourcePlanContextSummary(plan);
            const themes = plan.visual_context?.themes?.slice(0, 3) || [];
            return <tr key={plan.id}>
              <td className="rrugc-source-cell">
                <div className="rrugc-source-file">
                  {plan.source_web_url ? (
                    <a href={plan.source_web_url} target="_blank" rel="noreferrer" className="rrugc-source-thumb" title="Open source in Google Drive">
                      <img src={plan.source_preview_url} alt={plan.source_name} loading="lazy" />
                    </a>
                  ) : (
                    <span className="rrugc-source-thumb"><img src={plan.source_preview_url} alt={plan.source_name} loading="lazy" /></span>
                  )}
                  <span>
                    <strong title={plan.source_name}>{plan.source_name}</strong>
                    <small title={plan.source_relative_path}>{plan.source_relative_path}</small>
                    <em>{sourceMeta(plan) || "Image source"}</em>
                  </span>
                </div>
              </td>
              <td className="rrugc-source-plan-context">
                <div className="rrugc-source-context-head">
                  <span className={"rrugc-source-plan-status tone-" + planTone(plan)}>
                    {planStatusLabel(plan)}
                  </span>
                  {plan.analyzed_at && <small>Analyzed {new Date(plan.analyzed_at).toLocaleString()}</small>}
                </div>
                <p title={context}>{context}</p>
                {themes.length > 0 && <div className="rrugc-source-theme-chips">
                  {themes.map(theme => <span key={theme}>{theme}</span>)}
                </div>}
                <div className="rrugc-source-query-chips">
                  {plan.search_queries.slice(0, 4).map(keyword => <span key={keyword} title={keyword}>{keyword}</span>)}
                  {plan.search_queries.length > 4 && <span>+{plan.search_queries.length - 4}</span>}
                  {plan.search_queries.length === 0 && <small>{plan.status === "ready" ? "No search query" : "Waiting for AI search plan…"}</small>}
                </div>
                {plan.last_error_code && <small className="rrugc-source-error">{plan.last_error_code}</small>}
              </td>
              <td className="rrugc-source-scout-cell">
                <div className="rrugc-source-progress-copy">
                  <strong>{plan.progress_count}<small>/{plan.target_count}</small></strong>
                  <span>{progress}%</span>
                </div>
                <span className="rrugc-progress rrugc-source-progress"><i style={{ width: progress + "%" }} /></span>
                <div className="rrugc-source-scout-meta">
                  <span className={"rrugc-agent status-" + (plan.scout_status || "offline")}>
                    {(plan.scout_status || "offline").replaceAll("_", " ")}
                  </span>
                  <small>{plan.pipeline_count} in pipeline · {plan.candidate_count} found</small>
                </div>
                {plan.campaign_id && <button type="button" onClick={() => onOpenCampaign(plan.campaign_id as string)}>
                  Open campaign
                </button>}
              </td>
              <td className="rrugc-source-refs-cell">
                <div className="rrugc-source-refs-head">
                  <strong>{plan.reference_previews.length} refs shown</strong>
                  <small>{plan.drive_ready_count} Drive ready · {plan.approved_count} qualified</small>
                </div>
                <ReferenceSlots plan={plan} />
              </td>
            </tr>;
          })}
          {shown.length === 0 && <tr>
            <td colSpan={4} className="rrugc-source-plan-empty">
              {plans.length === 0
                ? "No source images yet. Sync the configured Drive folder to create one 20-ref plan per image."
                : "No source plan matches this filter."}
            </td>
          </tr>}
        </tbody>
      </table>
    </div>

    {filtered.length > visibleRows && <div className="rrugc-source-plan-more">
      <button type="button" onClick={() => setVisibleRows(value => value + INITIAL_VISIBLE_ROWS)}>
        Show {Math.min(INITIAL_VISIBLE_ROWS, filtered.length - visibleRows)} more
      </button>
      <small>{visibleRows} of {filtered.length} visible</small>
    </div>}
  </section>;
}
