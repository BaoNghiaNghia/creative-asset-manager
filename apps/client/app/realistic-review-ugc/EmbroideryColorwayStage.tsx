import { useEffect, useMemo, useState } from "react";
import { ActionMessageToast } from "../components/ActionToast";
import { listStage2Skills, listColorwayJobs, queueColorwayBatch, retryColorway, regenerateColorwayJob, getColorwayReadiness } from "./api";
import type { ColorwayJob, ColorwayReadiness } from "./api";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { RrugcActionIcon } from "./RrugcActionIcon";
import { OutputActivityLoader } from "./OutputActivityLoader";
import { SkillJobLogDialog } from "./SkillJobLogDialog";
import { GenerationOutputVersionsDialog } from "./GenerationOutputVersionsDialog";
import { RrugcSmartSearchInput } from "./RrugcSmartSearchInput";
import type { SourcePlan, SourcePlanPage, Stage2Skill, Stage2SkillCatalog, Stage2SkillSelection } from "./types";

const COLOR_KEYS = ["khaki-maroon", "natural-black", "natural-brown", "natural-camo-green", "natural-charcoal", "natural-forest-green", "natural-khaki", "natural-maroon", "natural-mossy-oak-breakup", "natural-navy", "natural-realtree-all-purpose", "natural-red", "natural-royal"] as const;
const COLOR_SLOT_COUNT = COLOR_KEYS.length;
const PREFERRED_COLORWAY_SKILL = "gatorhats-8869-scale-image";
const STAGE1_PAGE_SIZE_OPTIONS = [20, 50, 100, 500] as const;
const EMPTY_CATALOG: Stage2SkillCatalog = {
  openai_configured: false,
  openai_status: "not_configured",
  error_code: null,
  items: [],
};

function cleanDesignName(name: string): string {
  return name
    .replace(/^embroidery_/i, "")
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim() || name;
}

function skillKey(skill: Stage2Skill): string {
  return [skill.source, skill.skill_id || skill.skill_name].join(":");
}

function colorSlotLabel(index: number): string {
  return String(index + 1).padStart(2, "0");
}

function HatSlotIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M5.2 13.1c.5-4.5 3.2-7.2 6.8-7.2s6.3 2.7 6.8 7.2" />
    <path d="M4.1 13.1h14.7c1.4 0 2.4.5 2.8 1.4-2.4 1.7-5.6 2.5-9.6 2.5-4 0-7.2-.8-9.6-2.5.3-.9.9-1.4 1.7-1.4Z" />
    <path d="M12 6v7" />
  </svg>;
}

function DesignSource({ plan }: { plan: SourcePlan }) {
  const groupCount = Math.max(1, plan.source_group_images?.length || plan.embroidery_group_size || 1);
  return <div className="rrugc-colorway-source">
    <button type="button" className="rrugc-colorway-thumb" title={plan.source_name}>
      <img src={plan.source_preview_url} alt={cleanDesignName(plan.source_name)} loading="lazy" />
      <span className="rrugc-colorway-prefix">JPG / PNG</span>
    </button>
    <div>
      <strong>{cleanDesignName(plan.source_name)}</strong>
      <small title={plan.source_relative_path}>{plan.source_relative_path}</small>
      <div className="rrugc-colorway-source-meta">
        <span>{groupCount} source {groupCount === 1 ? "image" : "images"}</span>
        {plan.source_width && plan.source_height
          ? <span>{plan.source_width}×{plan.source_height}</span>
          : null}
      </div>
    </div>
  </div>;
}

export function EmbroideryColorwayStage({
  data,
  loading,
  syncing,
  query,
  message,
  executionReady = true,
  active = true,
  skillCatalogRevision = 0,
  onSync,
  onQueryChange,
  onPageChange,
  onPageSizeChange,
}: {
  data: SourcePlanPage;
  loading: boolean;
  syncing: boolean;
  query: string;
  message?: string;
  executionReady?: boolean;
  active?: boolean;
  skillCatalogRevision?: number;
  onSync: () => void;
  onQueryChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const [catalog, setCatalog] = useState<Stage2SkillCatalog>(EMPTY_CATALOG);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogMessage, setCatalogMessage] = useState("");
  const [selectedSkillKey, setSelectedSkillKey] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [colorwayJobs, setColorwayJobs] = useState<ColorwayJob[]>([]);
  const [queueing, setQueueing] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [jobMessage, setJobMessage] = useState("");
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [versionsJob, setVersionsJob] = useState<{ jobId: string; title: string } | null>(null);
  const [serverReadiness, setServerReadiness] = useState<ColorwayReadiness | null>(null);
  const [readinessMessage, setReadinessMessage] = useState("");

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setServerReadiness(null);
    setReadinessMessage("");
    void getColorwayReadiness(controller.signal)
      .then(value => {
        if (!controller.signal.aborted) setServerReadiness(value);
      })
      .catch(reason => {
        if (!controller.signal.aborted) setReadinessMessage(
          reason instanceof Error ? reason.message : "Could not check colorway readiness.",
        );
      });
    return () => controller.abort();
  }, [active, skillCatalogRevision]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setCatalogLoading(true);
    void listStage2Skills(false, controller.signal)
      .then(result => {
        setCatalog(result);
        const firstReady = result.items.find(item => item.ready && skillKey(item) === result.stage_defaults?.stage2)
          || result.items.find(item => item.skill_name === PREFERRED_COLORWAY_SKILL && item.ready)
          || result.items.find(item => item.ready) || result.items[0];
        if (firstReady) setSelectedSkillKey(current => result.stage_defaults?.stage2 ? skillKey(firstReady) : current || skillKey(firstReady));
      })
      .catch(reason => {
        if (!controller.signal.aborted) {
          setCatalogMessage(reason instanceof Error ? reason.message : "Unable to load skills.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCatalogLoading(false);
      });
    return () => controller.abort();
  }, [active, skillCatalogRevision]);

  const selectedSkill = useMemo(
    () => catalog.items.find(item => skillKey(item) === selectedSkillKey) || null,
    [catalog.items, selectedSkillKey],
  );
  const pageCount = Math.max(1, Math.ceil(data.total / Math.max(1, data.page_size)));
  const plannedOutputs = data.total * COLOR_SLOT_COUNT;
  const pageIds = data.items.map(item => item.id);
  const pageSelected = pageIds.length > 0 && pageIds.every(id => selectedIds.has(id));
  const ready = Boolean(selectedSkill?.ready);
  const canQueue = executionReady && Boolean(serverReadiness?.ready) && ready && selectedIds.size > 0 && !queueing;
  const readinessWarning = serverReadiness && !serverReadiness.ready
    ? serverReadiness.error_code === "colorway_stock_missing"
      ? "Missing or invalid stock photos: " + serverReadiness.stock_ready_count + "/" + serverReadiness.stock_total_count + " verified."
      : serverReadiness.error_code === "colorway_runtime_missing"
        ? "Codex CLI or the 8869 scale Skill is missing from the server runtime."
        : "The image generation service is not enabled for Stage 2."
    : readinessMessage;
  const pagePlanIds = data.items.map(item => item.id);
  const pagePlanKey = pagePlanIds.join(",");
  const byPlan = new Map<string, Map<string, ColorwayJob>>();
  for (const job of colorwayJobs) {
    if (!byPlan.has(job.source_plan_id)) byPlan.set(job.source_plan_id, new Map());
    byPlan.get(job.source_plan_id)!.set(job.color_key, job);
  }
  const skillInput: Stage2SkillSelection | null = ready && selectedSkill ? {
    source: selectedSkill.source, skill_id: selectedSkill.skill_id,
    skill_name: selectedSkill.skill_name,
    skill_version: selectedSkill.synced_version || selectedSkill.default_version || selectedSkill.local_version || null,
  } : null;

  useEffect(() => {
    if (!active || !pagePlanKey) return;
    const controller = new AbortController();
    let busy = false;
    let timer: number | undefined;
    let refreshDelay = 30000;
    const schedule = () => {
      window.clearTimeout(timer);
      if (!controller.signal.aborted) {
        timer = window.setTimeout(() => {
          if (document.hidden) schedule();
          else void refresh();
        }, refreshDelay);
      }
    };
    const refresh = async () => {
      if (busy || controller.signal.aborted) return;
      window.clearTimeout(timer);
      busy = true;
      try {
        const result = await listColorwayJobs(pagePlanKey.split(","), controller.signal);
        if (!controller.signal.aborted) {
          setColorwayJobs(result);
          // Poll quickly only while a job can change; idle pages should not hammer the API.
          refreshDelay = result.some(job => job.status === "queued" || job.status === "running") ? 5000 : 30000;
        }
      } catch (reason) {
        if (!controller.signal.aborted) setJobMessage(reason instanceof Error ? reason.message : "Could not load colorway jobs.");
        refreshDelay = 30000;
      } finally {
        busy = false;
        schedule();
      }
    };
    const onVisibilityChange = () => {
      if (!document.hidden && !busy) void refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    void refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [active, pagePlanKey]);

  const refreshJobs = async () => {
    if (pagePlanKey) setColorwayJobs(await listColorwayJobs(pagePlanKey.split(",")));
  };
  const queueSelected = async () => {
    if (!skillInput || !canQueue) return;
    setQueueing(true);
    setJobMessage("");
    try {
      const ids = Array.from(selectedIds);
      let queued = 0;
      for (let start = 0; start < ids.length; start += 50) {
        const result = await queueColorwayBatch(ids.slice(start, start + 50), skillInput);
        queued += result.queued;
      }
      await refreshJobs();
      setJobMessage(queued ? queued + " colorway jobs queued. Completed colors will be preserved." : "All selected colors are already queued or completed.");
    } catch (reason) {
      setJobMessage(reason instanceof Error ? reason.message : "Unable to queue colorways.");
    } finally { setQueueing(false); }
  };
  const retryFailed = async (job: ColorwayJob) => {
    if (retryingId) return;
    setRetryingId(job.id);
    setJobMessage("");
    try {
      await retryColorway(job.id);
      await refreshJobs();
      setJobMessage("Retry queued for " + job.color_name + ".");
    } catch (reason) {
      setJobMessage(reason instanceof Error ? reason.message : "Unable to retry colorway.");
    } finally { setRetryingId(null); }
  };
  const regenerateCompleted = async (jobId: string) => {
    if (retryingId) return;
    setRetryingId(jobId);
    try {
      await regenerateColorwayJob(jobId);
      await refreshJobs();
      setJobMessage("New colorway version queued. Existing images stay available.");
      setVersionsJob(null);
    } catch (reason) {
      setJobMessage(reason instanceof Error ? reason.message : "Unable to regenerate colorway.");
    } finally { setRetryingId(null); }
  };
  const searchSuggestions = useMemo(() => data.items.flatMap(plan => {
    const folder = plan.source_relative_path.includes("/")
      ? plan.source_relative_path.split("/").slice(0, -1).join("/")
      : "";
    return [
      { value: cleanDesignName(plan.source_name), meta: folder || "Embroidery design", badge: "Design" },
      ...(folder ? [{ value: folder, meta: cleanDesignName(plan.source_name), badge: "Folder" }] : []),
    ];
  }), [data.items]);

  function togglePage() {
    setSelectedIds(current => {
      const next = new Set(current);
      if (pageSelected) pageIds.forEach(id => next.delete(id));
      else pageIds.forEach(id => next.add(id));
      return next;
    });
  }

  function toggleDesign(id: string) {
    setSelectedIds(current => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return <section className="rrugc-card rrugc-colorway-stage">
    <RrugcStageHeader
      className="rrugc-colorway-header"
      kicker="STAGE 2 · DRIVE IMAGES → SKILL → 13 HAT COLORS"
      title="Embroidery design → 13 colorways"
      description={<>Scans JPG/PNG images in the configured Drive root and all subfolders, regardless of the filename prefix. Generated <code>output_</code> files are excluded. Each design can create 13 colorways independently of Pinterest.</>}
      actions={<div className="rrugc-colorway-header-actions">
        <span className="rrugc-colorway-scan-badge"><i aria-hidden="true" />Drive · JPG/PNG · recursive</span>
        <button type="button" className="rrugc-primary rrugc-icon-action" disabled={syncing} onClick={onSync}>
          <RrugcActionIcon name={syncing ? "refresh" : "scan"} />
          {syncing ? "Scanning…" : "Scan source"}
        </button>
      </div>}
    />

    <ActionMessageToast message={message} />
    <ActionMessageToast message={jobMessage} />
    <ActionMessageToast message={catalogMessage} tone="info" />
    {readinessWarning && <p className="rrugc-editor-product-result" role="status">{readinessWarning}</p>}

    <div className="rrugc-colorway-kpis">
      <article><span>Designs found</span><strong>{data.total}</strong><small>JPG / PNG source images</small></article>
      <article><span>Hat colors</span><strong>{COLOR_SLOT_COUNT}</strong><small>per design</small></article>
      <article><span>Planned outputs</span><strong>{plannedOutputs}</strong><small>design × color</small></article>
      <article><span>Selected</span><strong>{selectedIds.size}</strong><small>{selectedIds.size * COLOR_SLOT_COUNT} outputs</small></article>
    </div>

    <div className="rrugc-colorway-toolbar">
      <div className="rrugc-colorway-search">
        <RrugcSmartSearchInput
          stageId="stage2"
          query={query}
          onQueryChange={onQueryChange}
          suggestions={searchSuggestions}
          placeholder="Search embroidery design or folder…"
          label="Search Stage 2 embroidery designs"
        />
      </div>
      <label className="rrugc-colorway-skill">
        <span>Generation skill</span>
        <select
          value={selectedSkillKey}
          disabled={catalogLoading || catalog.items.length === 0}
          onChange={event => setSelectedSkillKey(event.target.value)}
        >
          {catalog.items.length === 0 && <option value="">{catalogLoading ? "Loading skills…" : "No skill available"}</option>}
          {catalog.items.map(skill => (
            <option key={skillKey(skill)} value={skillKey(skill)}>
              {skill.display_name}{skill.ready ? "" : " · needs sync"}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="rrugc-colorway-select rrugc-icon-action" onClick={togglePage} disabled={loading || data.items.length === 0}>
        <RrugcActionIcon name={pageSelected ? "deselect" : "select-all"} />
        {pageSelected ? "Clear page" : "Select page"}
      </button>
      <button
        type="button"
        className="rrugc-colorway-run rrugc-icon-action"
        disabled={!canQueue}
        onClick={() => void queueSelected()}
        title={!serverReadiness?.ready ? "Stage 2 server readiness is not confirmed" : ready ? "Queue missing colors for selected designs" : "Selected skill is not ready"}
      >
        <RrugcActionIcon name={queueing ? "refresh" : "play"} />
        {queueing ? "Queueing…" : "Run selected · " + (selectedIds.size * COLOR_SLOT_COUNT)}
      </button>
    </div>

    <div className="rrugc-colorway-table-wrap">
      <table className="rrugc-colorway-table" aria-busy={loading}>
        <thead>
          <tr>
            <th aria-label="Select design" />
            <th>Embroidery design</th>
            <th>13-color batch</th>
            <th>Progress</th>
            <th>Skill</th>
          </tr>
        </thead>
        <tbody>
          {loading ? Array.from({ length: 5 }, (_, index) => (
            <tr key={index} className="rrugc-colorway-skeleton-row">
              <td><span /></td><td><span /></td><td><span /></td><td><span /></td><td><span /></td>
            </tr>
          )) : data.items.map(plan => {
            const selected = selectedIds.has(plan.id);
            const slots = byPlan.get(plan.id);
            const completed = Array.from(slots?.values() || []).filter(job => job.status === "completed").length;
            const failed = Array.from(slots?.values() || []).filter(job => job.status === "failed").length;
            const inProgress = Array.from(slots?.values() || []).filter(job => job.status === "running" || job.status === "queued").length;
            return <tr key={plan.id} className={selected ? "is-selected" : ""}>
              <td className="rrugc-colorway-check-cell">
                <input
                  type="checkbox"
                  checked={selected}
                  aria-label={"Select " + cleanDesignName(plan.source_name)}
                  onChange={() => toggleDesign(plan.id)}
                />
              </td>
              <td><DesignSource plan={plan} /></td>
              <td>
                <div className="rrugc-colorway-slots" aria-label="13 hat color output slots">
                  {Array.from({ length: COLOR_SLOT_COUNT }, (_, index) => {
                    const job = slots?.get(COLOR_KEYS[index]);
                    const label = job?.color_name || COLOR_KEYS[index].replaceAll("-", " ");
                    const contents = <><HatSlotIcon /><b>{colorSlotLabel(index)}</b></>;
                    const style = "rrugc-colorway-slot is-" + (job?.status || "pending");
                    if (job?.status === "completed" && job.output_url) {
                      return <button type="button" key={index} className={style} onClick={() => setVersionsJob({ jobId: job.id, title: label + " · " + cleanDesignName(plan.source_name) })} title={label + " · View all output versions"} aria-label={label + " · View output versions"}>{contents}</button>;
                    }
                    if (job?.status === "failed") {
                      return <button type="button" key={index} className={style} disabled={Boolean(retryingId) || job.retry_count >= 3}
                        onClick={() => void retryFailed(job)} title={label + " · " + (job.error_code || "Failed") + (job.retry_count >= 3 ? " · Retry limit" : " · Click to retry")}
                        aria-label={"Retry " + label}>{contents}</button>;
                    }
                    return <span key={index} className={style} title={label + " · " + (job?.status || "Not run")}>
                      {job?.status === "running" || job?.status === "queued"
                        ? <><OutputActivityLoader compact status={job.status} /><b>{colorSlotLabel(index)}</b></>
                        : contents}
                    </span>;
                  })}
                </div>
              </td>
              <td>
                <div className="rrugc-colorway-progress">
                  <div><span style={{ width: Math.round(completed / COLOR_SLOT_COUNT * 100) + "%" }} /></div>
                  <strong>{completed} / {COLOR_SLOT_COUNT}</strong>
                  <small>{failed ? failed + " failed · click to retry" : inProgress ? inProgress + " queued/running" : completed === COLOR_SLOT_COUNT ? "Completed" : "Ready to queue"}</small>
                  {!!slots?.size && <select aria-label={"Job logs for " + plan.source_name} value="" onChange={event => setLogJobId(event.target.value || null)}><option value="">View skill logs…</option>{Array.from(slots.values()).map(job => <option key={job.id} value={job.id}>{job.color_name} · {job.status}</option>)}</select>}
                </div>
              </td>
              <td>
                <div className="rrugc-colorway-skill-state">
                  <span className={ready ? "is-ready" : "is-warning"}><i aria-hidden="true" />{ready ? "Ready" : "Needs skill"}</span>
                  <strong>{selectedSkill?.display_name || "Select skill"}</strong>
                  <small>{completed} completed · {inProgress} active · {failed} failed</small>
                </div>
              </td>
            </tr>;
          })}
          {!loading && data.items.length === 0 && <tr>
            <td colSpan={5} className="rrugc-source-plan-empty">
              No JPG/PNG images have been indexed yet. Check access to the Drive folder and its subfolders, then click Scan source.
            </td>
          </tr>}
        </tbody>
      </table>
    </div>

    <footer className="rrugc-colorway-footer">
      <span>
        Page {data.page} / {pageCount} · {data.total} designs · {plannedOutputs} planned outputs
      </span>
      <div>
        <button type="button" disabled={loading || data.page <= 1} onClick={() => onPageChange(Math.max(1, data.page - 1))} className="rrugc-icon-action"><RrugcActionIcon name="chevron-left" />Previous</button>
        <button type="button" disabled={loading || data.page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, data.page + 1))} className="rrugc-icon-action">Next<RrugcActionIcon name="chevron-right" /></button>
        <label>Rows <select aria-label="Stage 2 rows per page" value={data.page_size} disabled={loading} onChange={event => onPageSizeChange(Number(event.target.value))}>
          {STAGE1_PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
      </div>
    </footer>
    {logJobId && <SkillJobLogDialog stage="stage2" jobId={logJobId} onClose={() => setLogJobId(null)} />}
    {versionsJob && <GenerationOutputVersionsDialog stage="stage2" jobId={versionsJob.jobId} title={versionsJob.title} onClose={() => setVersionsJob(null)} onRegenerate={() => void regenerateCompleted(versionsJob.jobId)} />}
  </section>;
}
