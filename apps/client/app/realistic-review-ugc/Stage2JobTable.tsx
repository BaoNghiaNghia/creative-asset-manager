import { useEffect, useMemo, useRef, useState } from "react";
import {
  listStage2Skills,
  stage2JobOutputThumbnailUrl,
  stage2JobOutputUrl,
  regenerateStage4Job,
} from "./api";
import { DeferredImage } from "./DeferredImage";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { SkillJobLogDialog } from "./SkillJobLogDialog";
import { GenerationOutputVersionsDialog } from "./GenerationOutputVersionsDialog";
import { RrugcSmartSearchInput } from "./RrugcSmartSearchInput";
import { SourceImageGroup, sourcePlanPageCount } from "./SourcePlanTable";
import { useHorizontalDragScroll } from "./useHorizontalDragScroll";
import type {
  SourcePlan,
  SourcePlanOverview,
  SourcePlanReferencePreview,
  Stage2Job,
  Stage2Skill,
  Stage2SkillCatalog,
  Stage2SkillSelection,
} from "./types";

const REFS_PER_RUN = 3;
const MAX_OUTPUT_SLOTS = 10;
const STAGE2_PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
const FALLBACK_SKILL_NAME = "gatorhats-8869-image-studio";

const FALLBACK_SKILL: Stage2Skill = {
  source: "local",
  skill_id: null,
  skill_name: FALLBACK_SKILL_NAME,
  display_name: "GatorHats 8869 Image Studio",
  description: "Local image studio skill.",
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

function stage2JobsByPlan(plans: SourcePlan[], jobs: Stage2Job[]) {
  const result = new Map<string, Stage2Job[]>();
  for (const plan of plans) {
    const memberIds = new Set([
      plan.id,
      ...(plan.source_group_images || []).map(member => member.id),
    ]);
    result.set(
      plan.id,
      jobs.filter(job => memberIds.has(job.source_plan_id)),
    );
  }
  return result;
}

function completedCandidateIdsByPlan(
  plans: SourcePlan[],
  allJobsByPlan: Map<string, Stage2Job[]>,
) {
  const result = new Map<string, Set<string>>();
  for (const plan of plans) {
    const completed = new Set<string>();
    for (const job of allJobsByPlan.get(plan.id) || []) {
      if (job.status !== "completed") continue;
      for (const candidateId of job.selected_candidate_ids) completed.add(candidateId);
    }
    result.set(plan.id, completed);
  }
  return result;
}

function recentJobsByPlan(
  plans: SourcePlan[],
  allJobsByPlan: Map<string, Stage2Job[]>,
) {
  const result = new Map<string, Stage2Job[]>();
  for (const plan of plans) {
    const newest = (allJobsByPlan.get(plan.id) || []).slice(0, MAX_OUTPUT_SLOTS);
    result.set(plan.id, [...newest].reverse());
  }
  return result;
}

function jobLabel(job: Stage2Job) {
  if (job.status === "queued") return "Queued";
  if (job.status === "running") return "Generating";
  if (job.status === "completed") return "Completed";
  if (job.status === "cancelled") return "Cancelled";
  return "Failed";
}

function outputSummary(jobs: Stage2Job[]) {
  const completed = jobs.filter(job => job.status === "completed").length;
  const failed = jobs.filter(job => job.status === "failed").length;
  const cancelled = jobs.filter(job => job.status === "cancelled").length;
  const active = jobs.filter(job => job.status === "queued" || job.status === "running").length;
  return {
    completed,
    failed,
    cancelled,
    active,
    remaining: Math.max(0, MAX_OUTPUT_SLOTS - jobs.length),
  };
}

const STAGE2_REF_CARD_PITCH = 70;
const STAGE2_REF_WINDOW_OVERSCAN = 3;
const STAGE2_REF_WINDOW_MIN = 14;

export function Stage2ReferenceReviewModal({
  planId,
  planName,
  references,
  selected,
  generated,
  busy,
  onToggle,
  onClose,
}: {
  planId: string;
  planName: string;
  references: SourcePlanReferencePreview[];
  selected: string[];
  generated: ReadonlySet<string>;
  busy: boolean;
  onToggle: (planId: string, referenceId: string) => void;
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

  return <div
    className="rrugc-source-review-backdrop"
    role="presentation"
    onMouseDown={event => event.target === event.currentTarget && onClose()}
  >
    <section
      className="rrugc-source-review-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={"rrugc-stage2-reference-review-title-" + planId}
    >
      <header className="rrugc-source-review-header">
        <div>
          <small>STAGE 3 REFERENCE PREVIEW</small>
          <h2 id={"rrugc-stage2-reference-review-title-" + planId}>{planName}</h2>
          <p>{references.length} images · {selected.length} selected</p>
        </div>
        <button
          type="button"
          className="rrugc-source-review-close"
          aria-label="Close Pinterest reference preview"
          onClick={onClose}
        >×</button>
      </header>
      <div className="rrugc-source-review-masonry rrugc-stage2-reference-review-masonry">
        {references.map((reference, index) => {
          const alreadyGenerated = generated.has(reference.id);
          const checked = !alreadyGenerated && selected.includes(reference.id);
          return <article
            key={reference.id}
            className={
              "rrugc-source-review-card rrugc-stage2-reference-review-card"
              + (checked ? " is-stage2-selected" : "")
              + (alreadyGenerated ? " is-stage2-generated" : "")
            }
          >
            <div className="rrugc-source-review-image">
              <DeferredImage
                src={reference.image_url}
                alt=""
                rootMargin="320px 0px"
                referrerPolicy="no-referrer"
              />
              <span className="rrugc-source-review-index">{index + 1}</span>
              <button
                type="button"
                className="rrugc-stage2-review-toggle"
                aria-label={
                  alreadyGenerated
                    ? "Reference " + (index + 1) + " already generated"
                    : checked
                      ? "Remove reference " + (index + 1)
                      : "Select reference " + (index + 1)
                }
                aria-pressed={checked}
                disabled={alreadyGenerated || busy}
                title={
                  alreadyGenerated
                    ? "Already generated"
                    : checked
                      ? "Remove reference"
                      : "Use this Pinterest reference"
                }
                onClick={() => onToggle(planId, reference.id)}
              >{alreadyGenerated || checked ? "✓" : "+"}</button>
            </div>
            <footer>
              <span>{alreadyGenerated ? "Already generated" : checked ? "Selected" : "Available"}</span>
              {reference.pin_url && <a href={reference.pin_url} target="_blank" rel="noreferrer">Pinterest ↗</a>}
            </footer>
          </article>;
        })}
      </div>
    </section>
  </div>;
}

function Stage2ReferencePicker({
  planId,
  planName,
  references,
  selected,
  generated,
  busy,
  onToggle,
}: {
  planId: string;
  planName: string;
  references: SourcePlanReferencePreview[];
  selected: string[];
  generated: ReadonlySet<string>;
  busy: boolean;
  onToggle: (planId: string, referenceId: string) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const { dragging, dragHandlers } = useHorizontalDragScroll();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [windowRange, setWindowRange] = useState({ start: 0, end: STAGE2_REF_WINDOW_MIN });
  const limited = references;

  function updateWindow() {
    const track = trackRef.current;
    if (!track) return;
    const visibleCount = Math.max(
      STAGE2_REF_WINDOW_MIN,
      Math.ceil(track.clientWidth / STAGE2_REF_CARD_PITCH) + STAGE2_REF_WINDOW_OVERSCAN * 2,
    );
    const firstVisible = Math.max(0, Math.floor(track.scrollLeft / STAGE2_REF_CARD_PITCH));
    const start = Math.max(0, firstVisible - STAGE2_REF_WINDOW_OVERSCAN);
    const end = Math.min(limited.length, start + visibleCount);
    setWindowRange(current => (
      current.start === start && current.end === end
        ? current
        : { start, end }
    ));
  }

  useEffect(() => {
    updateWindow();
    const track = trackRef.current;
    if (!track || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateWindow);
    observer.observe(track);
    return () => observer.disconnect();
  }, [limited.length]);

  if (limited.length === 0) {
    return <div className="rrugc-stage2-ref-grid">
      <small className="rrugc-stage2-empty-ref">Waiting for approved Pinterest references to finish Drive import.</small>
    </div>;
  }

  const start = Math.min(windowRange.start, Math.max(0, limited.length - 1));
  const end = Math.min(limited.length, Math.max(start + 1, windowRange.end));
  const visible = limited.slice(start, end);
  const leadingWidth = start > 0 ? Math.max(0, start * STAGE2_REF_CARD_PITCH - 6) : 0;
  const trailingCount = Math.max(0, limited.length - end);
  const trailingWidth = trailingCount > 0
    ? Math.max(0, trailingCount * STAGE2_REF_CARD_PITCH - 6)
    : 0;

  return <>
    <div
      ref={trackRef}
      className={"rrugc-stage2-ref-grid" + (dragging ? " is-dragging" : "")}
      aria-label={limited.length + " Drive-ready references for " + planName}
      onScroll={updateWindow}
      {...dragHandlers}
    >
    {leadingWidth > 0 && <span
      className="rrugc-stage2-ref-window-spacer"
      aria-hidden="true"
      style={{ flexBasis: leadingWidth }}
    />}
    {visible.map(reference => {
      const alreadyGenerated = generated.has(reference.id);
      const checked = !alreadyGenerated && selected.includes(reference.id);
      return <div
        key={reference.id}
        className={"rrugc-stage2-ref " + (alreadyGenerated ? "is-generated" : checked ? "is-selected" : "")}
      >
        <button
          type="button"
          className="rrugc-stage2-ref-open"
          aria-label={"Open reference preview for " + planName}
          title="Open reference preview"
          onClick={() => setReviewOpen(true)}
        >
          <DeferredImage src={reference.image_url} alt="" rootMargin="180px" referrerPolicy="no-referrer" />
        </button>
        <button
          type="button"
          className="rrugc-stage2-ref-toggle"
          aria-label={
            alreadyGenerated
              ? "Already generated"
              : checked
                ? "Remove reference"
                : "Select reference"
          }
          aria-pressed={checked}
          disabled={alreadyGenerated || busy}
          title={alreadyGenerated ? "Already generated" : checked ? "Remove reference" : "Use this Pinterest reference"}
          onClick={() => onToggle(planId, reference.id)}
        >{alreadyGenerated ? "✓" : checked ? "✓" : "+"}</button>
      </div>;
    })}
    {trailingWidth > 0 && <span
      className="rrugc-stage2-ref-window-spacer"
      aria-hidden="true"
      style={{ flexBasis: trailingWidth }}
    />}
    </div>
    {reviewOpen && <Stage2ReferenceReviewModal
      planId={planId}
      planName={planName}
      references={limited}
      selected={selected}
      generated={generated}
      busy={busy}
      onToggle={onToggle}
      onClose={() => setReviewOpen(false)}
    />}
  </>;
}

export function Stage2OutputReviewModal({
  plan,
  jobs,
  onClose,
  onRegenerateJob,
}: {
  plan: SourcePlan;
  jobs: Stage2Job[];
  onClose: () => void;
  onRegenerateJob?: (job: Stage2Job) => void;
}) {
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [versionsJobId, setVersionsJobId] = useState<string | null>(null);
  const [versionError, setVersionError] = useState("");
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  async function regenerate(jobId: string) {
    if (regeneratingId) return;
    setRegeneratingId(jobId);
    setVersionError("");
    try {
      await regenerateStage4Job(jobId);
      setVersionsJobId(null);
      onClose();
    } catch (reason) {
      setVersionError(reason instanceof Error ? reason.message : "Unable to generate a new version.");
    } finally {
      setRegeneratingId(null);
    }
  }
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
    <section className="rrugc-source-review-modal" role="dialog" aria-modal="true" aria-labelledby={"rrugc-stage2-output-review-title-" + plan.id}>
      <header className="rrugc-source-review-header">
        <div>
          <small>GENERATED OUTPUT PREVIEW</small>
          <h2 id={"rrugc-stage2-output-review-title-" + plan.id}>{plan.source_name}</h2>
          <p>{jobs.length} generated {jobs.length === 1 ? "output" : "outputs"}</p>
        </div>
        <button type="button" className="rrugc-source-review-close" aria-label="Close generated output preview" onClick={onClose}>×</button>
      </header>
      <div className="rrugc-source-review-masonry rrugc-stage2-output-review-masonry">
        {jobs.map((run, index) => (
          <article key={run.id} className="rrugc-source-review-card rrugc-stage2-output-review-card">
            <div className="rrugc-source-review-image">
              <DeferredImage
                src={stage2JobOutputUrl(run.id)}
                alt={"Generated output " + (index + 1)}
                rootMargin="320px 0px"
              />
              <span className="rrugc-source-review-index">{index + 1}</span>
            </div>
            <footer>
              <span>{new Date(run.completed_at || run.created_at).toLocaleString()}</span>
              {run.output_web_url && <a href={run.output_web_url} target="_blank" rel="noreferrer">Drive ↗</a>}
              <button type="button" onClick={() => setLogJobId(run.id)}>Logs</button>
              {onRegenerateJob && <button type="button" onClick={() => { onRegenerateJob(run); onClose(); }}>New version</button>}
              <button type="button" onClick={() => setVersionsJobId(run.id)}>Versions</button>
            </footer>
          </article>
        ))}
      </div>
      {versionError && <p role="alert">{versionError}</p>}
      {versionsJobId && <GenerationOutputVersionsDialog stage="stage4" jobId={versionsJobId}
        title={plan.source_name} onClose={() => setVersionsJobId(null)}
        onRegenerate={regeneratingId ? undefined : () => void regenerate(versionsJobId)} />}
      {logJobId && <SkillJobLogDialog stage="stage4" jobId={logJobId} onClose={() => setLogJobId(null)} />}
    </section>
  </div>;
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
    catalog.items.find(skill => skill.ready && skillKey(skill) === catalog.stage_defaults?.stage4)
    ||     catalog.items.find(skill => skill.ready && skill.skill_name === FALLBACK_SKILL_NAME)
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

function Stage2SkeletonRows({ count }: { count: number }) {
  const rows = Math.min(8, Math.max(3, count));
  return <>
    {Array.from({ length: rows }, (_, index) => <tr key={"stage2-skeleton-" + index} className="rrugc-table-skeleton-row" aria-hidden="true">
      <td><span className="rrugc-table-skeleton rrugc-table-skeleton-source" /></td>
      <td><span className="rrugc-table-skeleton rrugc-table-skeleton-refs" /></td>
      <td><span className="rrugc-table-skeleton rrugc-table-skeleton-copy" /><span className="rrugc-table-skeleton rrugc-table-skeleton-copy is-short" /></td>
      <td><span className="rrugc-table-skeleton rrugc-table-skeleton-status" /></td>
      <td><span className="rrugc-table-skeleton rrugc-table-skeleton-results" /></td>
    </tr>)}
  </>;
}

export function Stage2JobTable({
  plans,
  jobs,
  overview = fallbackStage2Overview(plans, jobs),
  total,
  page = 1,
  pageSize = 10,
  creatingPlanIds,
  cancellingPlanIds = new Set<string>(),
  loading = false,
  message,
  skillCatalogRevision = 0,
  query = "",
  onQueryChange = () => undefined,
  onCreateJob,
  onManageSkills = () => undefined,
  onCancelJobs = () => undefined,
  onRetryJob = () => undefined,
  onRegenerateJob = () => undefined,
  onPageChange = () => undefined,
  onPageSizeChange = () => undefined,
}: {
  plans: SourcePlan[];
  overview?: SourcePlanOverview;
  total?: number;
  page?: number;
  pageSize?: number;
  jobs: Stage2Job[];
  creatingPlanIds: ReadonlySet<string>;
  cancellingPlanIds?: ReadonlySet<string>;
  loading?: boolean;
  message?: string;
  skillCatalogRevision?: number;
  query?: string;
  onQueryChange?: (query: string) => void;
  onCreateJob: (
    plan: SourcePlan,
    candidateIds: string[],
    skill: Stage2SkillSelection,
  ) => void;
  onManageSkills?: () => void;
  onCancelJobs?: (plan: SourcePlan, jobIds: string[]) => void;
  onRetryJob?: (job: Stage2Job) => void;
  onRegenerateJob?: (job: Stage2Job) => void;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
}) {
  const [selectedByPlan, setSelectedByPlan] = useState<Record<string, string[]>>({});
  const [catalog, setCatalog] = useState<Stage2SkillCatalog>(INITIAL_CATALOG);
  const [skillKeyByPlan, setSkillKeyByPlan] = useState<Record<string, string>>({});
  const [skillVersionByPlan, setSkillVersionByPlan] = useState<Record<string, string>>({});
  const [tableLogJobId, setTableLogJobId] = useState<string | null>(null);
  const [refreshingSkills, setRefreshingSkills] = useState(false);
  const [skillMessage, setSkillMessage] = useState("");
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [outputReview, setOutputReview] = useState<{
    plan: SourcePlan;
    jobs: Stage2Job[];
  } | null>(null);
  const stage2Plans = plans;
  const allJobsByPlan = useMemo(
    () => stage2JobsByPlan(stage2Plans, jobs),
    [stage2Plans, jobs],
  );
  const recentJobs = useMemo(
    () => recentJobsByPlan(stage2Plans, allJobsByPlan),
    [stage2Plans, allJobsByPlan],
  );
  const completedCandidates = useMemo(
    () => completedCandidateIdsByPlan(stage2Plans, allJobsByPlan),
    [stage2Plans, allJobsByPlan],
  );
  const stage2Total = total ?? stage2Plans.length;
  const pageCount = sourcePlanPageCount(stage2Total, pageSize);
  const currentPage = Math.min(Math.max(1, page), pageCount);
  const pageStart = stage2Total === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const pageEnd = stage2Total === 0
    ? 0
    : Math.min((currentPage - 1) * pageSize + stage2Plans.length, stage2Total);
  const searchSuggestions = useMemo(() => stage2Plans.flatMap(plan => {
    const folder = plan.source_relative_path.includes("/")
      ? plan.source_relative_path.split("/").slice(0, -1).join("/")
      : "";
    return [
      { value: plan.source_name, meta: folder || "Embroidery group", badge: "Group" },
      ...(folder ? [{ value: folder, meta: plan.source_name, badge: "Folder" }] : []),
      ...plan.search_queries.slice(0, 2).map(value => ({ value, meta: plan.source_name, badge: "Query" })),
    ];
  }), [stage2Plans]);

  useEffect(() => {
    const controller = new AbortController();
    void listStage2Skills(false, controller.signal)
      .then(setCatalog)
      .catch(() => undefined);
    return () => controller.abort();
  }, [skillCatalogRevision]);

  useEffect(() => {
    setNowMs(Date.now());
    const hasCancelableJob = jobs.some(job => (
      job.can_cancel
      && Boolean(job.cancel_available_until)
      && Date.parse(job.cancel_available_until || "") > Date.now()
    ));
    if (!hasCancelableJob) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [jobs]);

  useEffect(() => {
    setSelectedByPlan(current => {
      let changed = false;
      const next = { ...current };
      for (const plan of stage2Plans) {
        const generated = completedCandidates.get(plan.id) || new Set<string>();
        if (Object.prototype.hasOwnProperty.call(next, plan.id)) {
          const filtered = next[plan.id].filter(referenceId => !generated.has(referenceId));
          if (filtered.length !== next[plan.id].length) {
            next[plan.id] = filtered;
            changed = true;
          }
          continue;
        }
        next[plan.id] = plan.reference_previews
          .filter(reference => reference.picked && eligibleReference(reference) && !generated.has(reference.id))
          .map(reference => reference.id);
        changed = true;
      }
      return changed ? next : current;
    });
  }, [stage2Plans, completedCandidates]);

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

  return <section className="rrugc-card rrugc-stage2">
    <RrugcStageHeader
      className="rrugc-stage2-heading"
      kicker="STAGE 4 · EMBROIDERY GROUP → PINTEREST REFS → SKILL"
      title="Pinterest references → image generation"
      description={<>Stage 3 grouping is preserved. Select any number of Drive-ready references; generation automatically runs them in groups of up to {REFS_PER_RUN} refs plus 1 random hat input until the selection is queued.</>}
      actions={<div className="rrugc-stage2-registry-actions">
        <span className="rrugc-source-auto-badge"><i aria-hidden="true" />{REFS_PER_RUN} refs + 1 random hat / run</span>
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
      </div>}
    />

    {message && <p className="rrugc-editor-product-result" role="status">{message}</p>}
    {skillMessage && <p className="rrugc-editor-product-result" role="status">{skillMessage}</p>}

    <div className="rrugc-source-plan-kpis rrugc-stage2-kpis">
      <article><span>Embroidery groups</span><strong>{overview.embroidery_groups}</strong></article>
      <article><span>Source images</span><strong>{overview.source_images}</strong></article>
      <article><span>Drive-ready refs</span><strong>{overview.stage2_drive_ready_refs}</strong></article>
      <article><span>Active jobs</span><strong>{overview.stage2_active_jobs}</strong></article>
    </div>

    <div className="rrugc-stage-search-toolbar rrugc-stage3-search-toolbar">
      <RrugcSmartSearchInput
        stageId="stage4"
        query={query}
        onQueryChange={onQueryChange}
        suggestions={searchSuggestions}
        placeholder="Search embroidery group, folder, or Pinterest query…"
        label="Search Stage 4 generation groups"
      />
      <span>{stage2Total.toLocaleString()} groups</span>
    </div>

    <div className="rrugc-source-plan-table-wrap">
      <table className="rrugc-source-plan-table rrugc-stage2-table" aria-busy={loading}>
        <thead>
          <tr>
            <th>Embroidery group</th>
            <th>References · unlimited selection</th>
            <th>Skill & generate</th>
            <th>Run status · latest 10</th>
            <th>Output</th>
          </tr>
        </thead>
        <tbody>
          {loading ? <Stage2SkeletonRows count={pageSize} /> : stage2Plans.map(plan => {
            const available = plan.reference_previews.filter(eligibleReference);
            const selected = selectedByPlan[plan.id] || [];
            const allPlanJobs = allJobsByPlan.get(plan.id) || [];
            const planJobs = recentJobs.get(plan.id) || [];
            const completedJobs = allPlanJobs.filter(job => job.status === "completed");
            const generated = completedCandidates.get(plan.id) || new Set<string>();
            const pickableAvailable = available.filter(reference => !generated.has(reference.id)).length;
            const runs = outputSummary(planJobs);
            const cancellableJobs = allPlanJobs.filter(job => (
              job.status === "queued"
              && job.can_cancel
              && Boolean(job.cancel_available_until)
              && Date.parse(job.cancel_available_until || "") > nowMs
            ));
            const cancelDeadlineMs = cancellableJobs.length
              ? Math.min(...cancellableJobs.map(job => Date.parse(job.cancel_available_until || "")))
              : null;
            const cancelSeconds = cancelDeadlineMs === null
              ? 0
              : Math.max(1, Math.ceil((cancelDeadlineMs - nowMs) / 1000));
            const cancelling = cancellingPlanIds.has(plan.id);
            const busy = creatingPlanIds.has(plan.id)
              || allPlanJobs.some(job => job.status === "queued" || job.status === "running");
            const skill = selectedSkill(plan.id);
            const version = selectedVersion(plan.id, skill);
            const hasReadySkill = catalog.items.length > 0;
            const canGenerate = hasReadySkill && skill.ready
              && (skill.source === "local" || version === skill.synced_version);
            const skillIssue = !hasReadySkill
              ? "No enabled and synced skill"
              : skill.sync_state === "local_conflict"
              ? "Local skill name conflict"
              : !skill.ready
                ? "Skill needs sync"
                : skill.source === "openai" && version !== skill.synced_version
                  ? "Selected version is not synced"
                  : "";
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
                  <strong>{selected.length} selected</strong>
                  <small>{pickableAvailable} refs available{generated.size ? " · " + generated.size + " generated" : ""}</small>
                </div>
                <Stage2ReferencePicker
                  planId={plan.id}
                  planName={plan.source_name}
                  references={available}
                  selected={selected}
                  generated={generated}
                  busy={busy}
                  onToggle={toggleReference}
                />
              </td>
              <td className="rrugc-stage2-skill">
                <div className="rrugc-stage2-cell-stack">
                <div className="rrugc-stage2-skill-controls">
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
                </div>
                <small className="rrugc-stage2-skill-id">{"$" + skill.skill_name} · {skillStatus(skill)}</small>
                {!canGenerate && skill.sync_state !== "local_conflict" && <button
                  type="button"
                  className="rrugc-stage2-sync"
                  disabled={busy}
                  onClick={onManageSkills}
                >
                  Manage / sync skill
                </button>}
                {skill.sync_state === "local_conflict" && <small className="rrugc-source-error">Rename the conflicting local skill before syncing.</small>}
                {cancelDeadlineMs !== null ? <>
                  <button
                    type="button"
                    className="rrugc-stage2-cancel"
                    disabled={cancelling}
                    onClick={() => onCancelJobs(plan, cancellableJobs.map(job => job.id))}
                  >
                    {cancelling ? "Cancelling…" : "Cancel · " + cancelSeconds + "s"}
                  </button>
                  <small className="rrugc-stage2-cancel-note">
                    Generation starts automatically when the 10-second cancel window ends.
                  </small>
                </> : <button
                  type="button"
                  className="rrugc-primary rrugc-stage2-generate"
                  disabled={busy || selected.length === 0 || !canGenerate}
                  onClick={() => onCreateJob(plan, selected, {
                    source: skill.source,
                    skill_id: skill.skill_id,
                    skill_name: skill.skill_name,
                    skill_version: version,
                  })}
                >
                  {creatingPlanIds.has(plan.id) ? "Queuing…" : busy ? "Generating…" : "Generate selected"}
                </button>}
                </div>
              </td>
              <td className="rrugc-stage2-status">
                <div className="rrugc-stage2-cell-stack">
                <div className="rrugc-stage2-output-head">
                  <strong>{planJobs.length}/{MAX_OUTPUT_SLOTS} runs</strong>
                  <small>
                    {runs.completed} done
                    {runs.failed ? " · " + runs.failed + " failed" : ""}
                    {runs.cancelled ? " · " + runs.cancelled + " cancelled" : ""}
                    {runs.active ? " · " + runs.active + " active" : ""}
                    {runs.remaining ? " · " + runs.remaining + " not run" : ""}
                  </small>
                </div>
                {!canGenerate && <div className="rrugc-stage2-output-blocked" role="status">
                  <span aria-hidden="true">!</span>
                  <div>
                    <strong>Generation blocked by skill</strong>
                    <small>{skillIssue || "Skill unavailable"}</small>
                  </div>
                </div>}
                <div className="rrugc-stage2-output-grid" aria-label={"Latest generation runs for " + plan.source_name}>
                  {Array.from({ length: MAX_OUTPUT_SLOTS }, (_, index) => {
                    const run = planJobs[index];
                    if (!run) {
                      return <div className="rrugc-stage2-run is-empty" key={"empty-" + index}>
                        <b>{index + 1}</b>
                        <small>Not run</small>
                      </div>;
                    }
                    if (run.status === "completed") {
                      return <div
                        className="rrugc-stage2-run is-completed"
                        key={run.id}
                        title={"Completed · " + new Date(run.created_at).toLocaleString()}
                      >
                        <span aria-hidden="true">✓</span>
                        <small>Done</small>
                      </div>;
                    }
                    if (run.status === "cancelled") {
                      return <div
                        className="rrugc-stage2-run is-cancelled"
                        key={run.id}
                        title="Cancelled during the 10-second grace period"
                      >
                        <span aria-hidden="true">×</span>
                        <small>Cancelled</small>
                      </div>;
                    }
                    if (run.status === "failed") {
                      return <div
                        className="rrugc-stage2-run is-failed"
                        key={run.id}
                        title={run.last_error_message || run.last_error_code || "Generation failed"}
                      >
                        <span aria-hidden="true">!</span>
                        <small>{run.last_error_code || "Failed"}</small>
                        <button type="button" className="rrugc-stage2-sync" onClick={() => onRetryJob(run)}>Retry</button>
                        <button type="button" className="rrugc-stage2-sync" onClick={() => setTableLogJobId(run.id)}>Logs</button>
                      </div>;
                    }
                    return <div
                      className={"rrugc-stage2-run is-" + run.status}
                      key={run.id}
                      title={jobLabel(run) + " · " + new Date(run.created_at).toLocaleString()}
                    >
                      <span className="rrugc-stage2-run-spinner" aria-hidden="true" />
                      <small>{jobLabel(run)}</small>
                    </div>;
                  })}
                </div>
                <div className="rrugc-stage2-run-legend" aria-label="Generation run status">
                  <span className="is-empty"><i />Not run</span>
                  <span className="is-active"><i />Running</span>
                  <span className="is-done"><i />Done</span>
                  <span className="is-cancelled"><i />Cancelled</span>
                  <span className="is-failed"><i />Failed</span>
                </div>
                </div>
              </td>
              <td className="rrugc-stage2-results">
                <div className="rrugc-stage2-cell-stack">
                <div className="rrugc-stage2-results-head">
                  <strong>{completedJobs.length} {completedJobs.length === 1 ? "output" : "outputs"}</strong>
                  <small>{completedJobs.length ? "Historical outputs are kept even while new runs are generating." : "Waiting for a completed generation."}</small>
                </div>
                {completedJobs.length ? <div className="rrugc-stage2-result-grid" aria-label={"Generated results for " + plan.source_name}>
                  {completedJobs.map((run, index) => <button
                    key={run.id}
                    type="button"
                    className="rrugc-stage2-result rrugc-stage2-result-open"
                    title={"Preview result " + (index + 1) + " · " + new Date(run.created_at).toLocaleString()}
                    aria-label={"Preview generated output " + (index + 1) + " for " + plan.source_name}
                    onClick={() => setOutputReview({
                      plan,
                      jobs: completedJobs,
                    })}
                  >
                    <DeferredImage
                      src={stage2JobOutputThumbnailUrl(run.id)}
                      alt={"Generated output " + (index + 1)}
                      rootMargin="180px"
                    />
                    <span>{index + 1}</span>
                  </button>)}
                </div> : <div className="rrugc-stage2-results-empty">
                  <strong>No results yet</strong>
                  <small>Completed generations will appear here.</small>
                </div>}
                </div>
              </td>
            </tr>;
          })}
          {!loading && stage2Plans.length === 0 && <tr><td colSpan={5} className="rrugc-source-plan-empty">Stage 4 jobs will appear here after Stage 3 finishes embroidery context analysis.</td></tr>}
        </tbody>
      </table>
    </div>

    <div className="rrugc-source-pagination rrugc-stage2-pagination">
      <span>{pageStart}–{pageEnd} of {stage2Total}</span>
      <div className="rrugc-source-page-controls">
        <button type="button" disabled={loading || currentPage <= 1} onClick={() => onPageChange(1)} aria-label="First Stage 4 page">«</button>
        <button type="button" disabled={loading || currentPage <= 1} onClick={() => onPageChange(Math.max(1, currentPage - 1))} aria-label="Previous Stage 4 page">‹</button>
        <strong>Page {currentPage} / {pageCount}</strong>
        <button type="button" disabled={loading || currentPage >= pageCount} onClick={() => onPageChange(Math.min(pageCount, currentPage + 1))} aria-label="Next Stage 4 page">›</button>
        <button type="button" disabled={loading || currentPage >= pageCount} onClick={() => onPageChange(pageCount)} aria-label="Last Stage 4 page">»</button>
      </div>
      <label>
        Rows
        <select
          value={pageSize}
          disabled={loading}
          onChange={event => onPageSizeChange(Number(event.target.value))}
          aria-label="Stage 4 rows per page"
        >
          {STAGE2_PAGE_SIZE_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
    </div>
    {tableLogJobId && <SkillJobLogDialog stage="stage4" jobId={tableLogJobId} onClose={() => setTableLogJobId(null)} />}
    {outputReview && <Stage2OutputReviewModal
      plan={outputReview.plan}
      jobs={outputReview.jobs}
      onClose={() => setOutputReview(null)}
    />}
  </section>;
}
