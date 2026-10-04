import { useEffect, useMemo, useState } from "react";
import {
  listStage2Skills,
  stage2JobOutputUrl,
  syncStage2Skill,
} from "./api";
import { SourceImageGroup } from "./SourcePlanTable";
import type {
  SourcePlan,
  SourcePlanOverview,
  SourcePlanReferencePreview,
  Stage2Job,
  Stage2Skill,
  Stage2SkillCatalog,
  Stage2SkillSelection,
} from "./types";

const MAX_REFS = 10;
const FALLBACK_SKILL_NAME = "gatorhats-8869-image-studio";

const FALLBACK_SKILL: Stage2Skill = {
  source: "local",
  skill_id: null,
  skill_name: FALLBACK_SKILL_NAME,
  display_name: "GatorHats 8869 Image Studio",
  description: "Local Stage 2 image studio skill.",
  default_version: null,
  latest_version: null,
  local_version: null,
  synced_version: null,
  ready: true,
  sync_state: "ready",
  version_options: [],
};

const INITIAL_CATALOG: Stage2SkillCatalog = {
  openai_configured: false,
  openai_status: "not_configured",
  error_code: null,
  items: [FALLBACK_SKILL],
};

function fallbackStage2Overview(plans: SourcePlan[], jobs: Stage2Job[]): SourcePlanOverview {
  const eligiblePlans = plans.filter(plan => (
    plan.status === "ready"
    && Boolean(plan.campaign_id)
    && Boolean(
      plan.embroidery_signature
      || plan.visual_context?.embroidery_identity
      || plan.visual_context?.embroidery_text?.length
    )
  ));
  return {
    embroidery_groups: plans.length,
    source_images: plans.reduce(
      (sum, plan) => sum + Math.max(1, plan.source_group_images?.length || plan.embroidery_group_size || 1),
      0,
    ),
    working_groups: 0,
    refs_loaded: 0,
    stage2_groups: eligiblePlans.length,
    stage2_source_images: eligiblePlans.reduce(
      (sum, plan) => sum + Math.max(1, plan.source_group_images?.length || plan.embroidery_group_size || 1),
      0,
    ),
    stage2_drive_ready_refs: eligiblePlans.reduce(
      (sum, plan) => sum + plan.reference_previews.filter(eligibleReference).length,
      0,
    ),
    stage2_active_jobs: jobs.filter(job => job.status === "queued" || job.status === "running").length,
  };
}

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

function skillKey(skill: Stage2Skill) {
  return skill.source + ":" + (skill.skill_id || skill.skill_name);
}

function skillStatus(skill: Stage2Skill) {
  if (skill.source === "local") return "Local · ready";
  if (skill.sync_state === "ready") return "OpenAI · synced";
  if (skill.sync_state === "update_available") return "OpenAI · update available";
  if (skill.sync_state === "local_conflict") return "OpenAI · local name conflict";
  return "OpenAI · needs sync";
}

function defaultSkill(catalog: Stage2SkillCatalog) {
  return (
    catalog.items.find(skill => skill.ready && skill.skill_name === FALLBACK_SKILL_NAME)
    || catalog.items.find(skill => skill.ready)
    || catalog.items[0]
    || FALLBACK_SKILL
  );
}

function defaultVersion(skill: Stage2Skill) {
  return (
    skill.synced_version
    || skill.default_version
    || skill.latest_version
    || skill.local_version
    || null
  );
}

function versionLabel(skill: Stage2Skill, version: string) {
  const badges: string[] = [];
  if (version === skill.default_version) badges.push("default");
  if (version === skill.latest_version && version !== skill.default_version) badges.push("latest");
  if (version === skill.synced_version) badges.push("synced");
  return "v" + version + (badges.length ? " · " + badges.join(" / ") : "");
}

export function Stage2JobTable({
  plans,
  jobs,
  overview = fallbackStage2Overview(plans, jobs),
  creatingPlanIds,
  message,
  onCreateJob,
}: {
  plans: SourcePlan[];
  overview?: SourcePlanOverview;
  jobs: Stage2Job[];
  creatingPlanIds: ReadonlySet<string>;
  message?: string;
  onCreateJob: (
    plan: SourcePlan,
    candidateIds: string[],
    skill: Stage2SkillSelection,
  ) => void;
}) {
  const [selectedByPlan, setSelectedByPlan] = useState<Record<string, string[]>>({});
  const [catalog, setCatalog] = useState<Stage2SkillCatalog>(INITIAL_CATALOG);
  const [skillKeyByPlan, setSkillKeyByPlan] = useState<Record<string, string>>({});
  const [skillVersionByPlan, setSkillVersionByPlan] = useState<Record<string, string>>({});
  const [refreshingSkills, setRefreshingSkills] = useState(false);
  const [syncingSkillKey, setSyncingSkillKey] = useState("");
  const [skillMessage, setSkillMessage] = useState("");
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
    const controller = new AbortController();
    void listStage2Skills(false, controller.signal)
      .then(setCatalog)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

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

  function selectedSkill(planId: string) {
    const requestedKey = skillKeyByPlan[planId];
    return (
      catalog.items.find(skill => skillKey(skill) === requestedKey)
      || defaultSkill(catalog)
    );
  }

  function selectedVersion(planId: string, skill: Stage2Skill) {
    return skillVersionByPlan[planId] || defaultVersion(skill);
  }

  function selectSkill(planId: string, nextKey: string) {
    const nextSkill = catalog.items.find(skill => skillKey(skill) === nextKey);
    setSkillKeyByPlan(current => ({ ...current, [planId]: nextKey }));
    setSkillVersionByPlan(current => {
      const next = { ...current };
      if (nextSkill) {
        const version = defaultVersion(nextSkill);
        if (version) next[planId] = version;
        else delete next[planId];
      }
      return next;
    });
    setSkillMessage("");
  }

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

  async function refreshSkills() {
    if (refreshingSkills) return;
    setRefreshingSkills(true);
    setSkillMessage("");
    try {
      setCatalog(await listStage2Skills(true));
      setSkillMessage("Skill registry refreshed.");
    } catch (reason) {
      setSkillMessage(reason instanceof Error ? reason.message : "Unable to refresh skills.");
    } finally {
      setRefreshingSkills(false);
    }
  }

  async function syncSkill(planId: string, skill: Stage2Skill, version: string | null) {
    if (!skill.skill_id || syncingSkillKey) return;
    const key = skillKey(skill);
    setSyncingSkillKey(key);
    setSkillMessage("");
    try {
      await syncStage2Skill(skill.skill_id, version);
      const refreshed = await listStage2Skills(true);
      setCatalog(refreshed);
      setSkillKeyByPlan(current => ({ ...current, [planId]: key }));
      if (version) {
        setSkillVersionByPlan(current => ({ ...current, [planId]: version }));
      }
      setSkillMessage(
        "$" + skill.skill_name + (version ? " v" + version : "") + " synced to Stage 2.",
      );
    } catch (reason) {
      setSkillMessage(reason instanceof Error ? reason.message : "Unable to sync skill.");
    } finally {
      setSyncingSkillKey("");
    }
  }

  return <section className="rrugc-card rrugc-stage2">
    <div className="rrugc-section-heading rrugc-stage2-heading">
      <div>
        <small>EMBROIDERY GROUP → PINTEREST REFS → SKILL</small>
        <h2>Embroidery groups → generation jobs</h2>
        <p>The same embroidery grouping from Stage 1 is preserved here. Pick up to 10 Drive-ready Pinterest references per group, choose a pinned skill/version, then generate one master image.</p>
      </div>
      <div className="rrugc-stage2-registry-actions">
        <span className="rrugc-source-auto-badge"><i aria-hidden="true" />Max {MAX_REFS} refs / job</span>
        <span className={"rrugc-source-auto-badge " + (catalog.openai_status === "error" ? "is-warning" : "")}>
          <i aria-hidden="true" />
          {catalog.openai_status === "connected"
            ? "OpenAI + local skills"
            : catalog.openai_status === "error"
              ? "OpenAI unavailable · local fallback"
              : "Local skills"}
        </span>
        <button
          type="button"
          className="rrugc-stage2-refresh"
          disabled={refreshingSkills}
          onClick={() => void refreshSkills()}
        >
          {refreshingSkills ? "Refreshing…" : "Refresh skills"}
        </button>
      </div>
    </div>

    {message && <p className="rrugc-editor-product-result" role="status">{message}</p>}
    {skillMessage && <p className="rrugc-editor-product-result" role="status">{skillMessage}</p>}

    <div className="rrugc-source-plan-kpis rrugc-stage2-kpis">
      <article><span>Embroidery groups</span><strong>{overview.stage2_groups}</strong></article>
      <article><span>Source images</span><strong>{overview.stage2_source_images}</strong></article>
      <article><span>Drive-ready refs</span><strong>{overview.stage2_drive_ready_refs}</strong></article>
      <article><span>Active jobs</span><strong>{overview.stage2_active_jobs}</strong></article>
    </div>

    <div className="rrugc-source-plan-table-wrap">
      <table className="rrugc-source-plan-table rrugc-stage2-table">
        <thead>
          <tr>
            <th>Embroidery group</th>
            <th>Pinterest refs · pick up to 10</th>
            <th>Skill / pinned version</th>
            <th>Job / output</th>
          </tr>
        </thead>
        <tbody>
          {stage2Plans.map(plan => {
            const available = plan.reference_previews.filter(eligibleReference);
            const selected = selectedByPlan[plan.id] || [];
            const job = latest.get(plan.id);
            const busy = creatingPlanIds.has(plan.id) || job?.status === "queued" || job?.status === "running";
            const skill = selectedSkill(plan.id);
            const version = selectedVersion(plan.id, skill);
            const canGenerate = skill.ready
              && (skill.source === "local" || version === skill.synced_version);
            const versionOptions = skill.version_options.length
              ? skill.version_options
              : version ? [version] : [];
            return <tr key={plan.id}>
              <td className="rrugc-source-cell">
                <div className="rrugc-source-file rrugc-source-file-grouped rrugc-stage2-source-group">
                  <SourceImageGroup plan={plan} />
                  <span>
                    <strong title={plan.source_name}>{plan.source_name}</strong>
                    <small>{plan.embroidery_group_size} source {plan.embroidery_group_size === 1 ? "image" : "images"}{plan.embroidery_group_size > 1 ? " · same embroidery" : ""}</small>
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
                <label>
                  <small>Skill</small>
                  <select
                    value={skillKey(skill)}
                    disabled={busy}
                    onChange={event => selectSkill(plan.id, event.target.value)}
                  >
                    {catalog.items.map(item => <option
                      key={skillKey(item)}
                      value={skillKey(item)}
                    >
                      {item.source === "openai" ? "OpenAI · " : "Local · "}
                      {item.display_name || item.skill_name}
                      {!item.ready ? " · needs sync" : ""}
                    </option>)}
                  </select>
                </label>
                <label>
                  <small>Version</small>
                  <select
                    value={version || ""}
                    disabled={busy || versionOptions.length <= 1}
                    onChange={event => setSkillVersionByPlan(current => ({
                      ...current,
                      [plan.id]: event.target.value,
                    }))}
                  >
                    {versionOptions.length === 0 && <option value="">Local current</option>}
                    {versionOptions.map(value => <option key={value} value={value}>
                      {versionLabel(skill, value)}
                    </option>)}
                  </select>
                </label>
                <strong>{"$" + skill.skill_name}</strong>
                <small>{skillStatus(skill)}</small>
                {skill.source === "openai" && !canGenerate && skill.sync_state !== "local_conflict" && <button
                  type="button"
                  className="rrugc-stage2-sync"
                  disabled={busy || syncingSkillKey === skillKey(skill)}
                  onClick={() => void syncSkill(plan.id, skill, version)}
                >
                  {syncingSkillKey === skillKey(skill)
                    ? "Syncing…"
                    : "Sync " + (version ? "v" + version : "skill")}
                </button>}
                {skill.sync_state === "local_conflict" && <small className="rrugc-source-error">Local skill with the same name is project-managed; rename one before syncing.</small>}
                <button
                  type="button"
                  className="rrugc-primary"
                  disabled={busy || selected.length === 0 || !canGenerate}
                  onClick={() => onCreateJob(plan, selected, {
                    source: skill.source,
                    skill_id: skill.skill_id,
                    skill_name: skill.skill_name,
                    skill_version: version,
                  })}
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
                {job && <small>{job.skill_source === "openai" ? "OpenAI" : "Local"} · {"$" + job.skill_name}{job.skill_version ? " · v" + job.skill_version : ""}</small>}
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
