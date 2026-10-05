import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import {
  createStage2Job,
  listSourcePlans,
  listStage2Jobs,
  markCandidateReferenceFeedback,
  syncSourcePlans,
  type SourcePlanSortBy,
  type SourcePlanSortDirection,
} from "./api";
import { PinterestAutoScoutPanel } from "./PinterestAutoScoutPanel";
import { SourcePlanTable } from "./SourcePlanTable";
import { Stage2JobTable } from "./Stage2JobTable";
import type { ReferenceManualLabel, SourcePlan, SourcePlanPage, SourcePlanReferencePreview, Stage2Job, Stage2SkillSelection } from "./types";
import "./ui-overhaul.css";
import "./tablet-mobile-density.css";

const EMPTY_SOURCE_PAGE: SourcePlanPage = {
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
  overview: {
    embroidery_groups: 0,
    source_images: 0,
    working_groups: 0,
    refs_loaded: 0,
    stage2_groups: 0,
    stage2_source_images: 0,
    stage2_drive_ready_refs: 0,
    stage2_active_jobs: 0,
  },
};

type RrugcStageTab = "stage1" | "stage2" | "settings";

const RRUGC_STAGE_TABS: Array<{ id: RrugcStageTab; label: string; description: string; marker: string }> = [
  { id: "stage1", label: "Stage 1", description: "Pinterest References", marker: "1" },
  { id: "stage2", label: "Stage 2", description: "Image Generation", marker: "2" },
  { id: "settings", label: "Settings", description: "Auto Scout", marker: "⚙" },
];

export function sourcePlanPageRenderFingerprint(page: SourcePlanPage): string {
  return JSON.stringify({
    page: page.page,
    page_size: page.page_size,
    total: page.total,
    overview: page.overview,
    items: page.items.map(plan => ({
      ...plan,
      // Scheduler timestamps can change without affecting anything visible in
      // the Stage 1 / Stage 2 tables. Excluding them prevents a full table
      // reconciliation every poll.
      updated_at: undefined,
      scan_next_at: undefined,
      scan_last_completed_at: undefined,
    })),
  });
}

export function stage2JobsRenderFingerprint(jobs: Stage2Job[]): string {
  return JSON.stringify(jobs.map(job => ({
    ...job,
    // updated_at may advance while a worker heartbeat/progress record is
    // persisted without changing anything rendered in Stage 2.
    updated_at: undefined,
  })));
}

export function RealisticReviewUgcPage() {
  const [sourcePage, setSourcePage] = useState<SourcePlanPage>(EMPTY_SOURCE_PAGE);
  const [sourcePageNumber, setSourcePageNumber] = useState(1);
  const [sourcePageSize, setSourcePageSize] = useState(10);
  const [sourceQuery, setSourceQuery] = useState("");
  const [debouncedSourceQuery, setDebouncedSourceQuery] = useState("");
  const [sourceSortBy, setSourceSortBy] = useState<SourcePlanSortBy>("source");
  const [sourceSortDirection, setSourceSortDirection] = useState<SourcePlanSortDirection>("asc");
  const [syncingSourcePlans, setSyncingSourcePlans] = useState(false);
  const [sourcePageLoading, setSourcePageLoading] = useState(true);
  const [reviewingReferenceIds, setReviewingReferenceIds] = useState<Set<string>>(new Set());
  const [sourcePlanMessage, setSourcePlanMessage] = useState("");
  const [stage2Jobs, setStage2Jobs] = useState<Stage2Job[]>([]);
  const [creatingStage2PlanIds, setCreatingStage2PlanIds] = useState<Set<string>>(new Set());
  const [stage2Message, setStage2Message] = useState("");
  const [error, setError] = useState("");
  const [activeStage, setActiveStage] = useState<RrugcStageTab>("stage1");
  const groupsStageActive = activeStage === "stage1" || activeStage === "stage2";

  const sourcePageCount = useMemo(
    () => Math.max(1, Math.ceil(sourcePage.total / Math.max(1, sourcePageSize))),
    [sourcePage.total, sourcePageSize],
  );

  async function refreshSourcePlans(signal?: AbortSignal) {
    const result = await listSourcePlans(
      {
        page: sourcePageNumber,
        pageSize: sourcePageSize,
        query: debouncedSourceQuery,
        sortBy: sourceSortBy,
        sortDirection: sourceSortDirection,
      },
      signal,
    );
    const nextFingerprint = sourcePlanPageRenderFingerprint(result);
    setSourcePage(current => (
      sourcePlanPageRenderFingerprint(current) === nextFingerprint
        ? current
        : result
    ));
  }

  async function refreshStage2Jobs(signal?: AbortSignal) {
    const result = await listStage2Jobs(undefined, signal);
    const nextFingerprint = stage2JobsRenderFingerprint(result);
    setStage2Jobs(current => (
      stage2JobsRenderFingerprint(current) === nextFingerprint
        ? current
        : result
    ));
  }

  async function queueStage2Job(
    plan: SourcePlan,
    candidateIds: string[],
    skill: Stage2SkillSelection,
  ) {
    if (creatingStage2PlanIds.has(plan.id) || candidateIds.length === 0) return;
    setCreatingStage2PlanIds(current => new Set(current).add(plan.id));
    setStage2Message("");
    setError("");
    try {
      const results = await Promise.allSettled(
        candidateIds.map(candidateId => createStage2Job(plan.id, [candidateId], skill)),
      );
      const queued = results.filter(
        (result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof createStage2Job>>> =>
          result.status === "fulfilled",
      );
      const failed = results.filter(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      if (queued.length === 0 && failed.length > 0) {
        throw failed[0].reason;
      }
      const createdCount = queued.filter(result => result.value.created).length;
      const existingCount = queued.length - createdCount;
      setStage2Message(
        createdCount + " output generation"
        + (createdCount === 1 ? "" : "s")
        + " queued from " + candidateIds.length + " selected Pinterest ref"
        + (candidateIds.length === 1 ? "" : "s")
        + (existingCount ? "; " + existingCount + " already existed" : "")
        + ".",
      );
      if (failed.length > 0) {
        setError(
          failed.length + " selected reference"
          + (failed.length === 1 ? "" : "s")
          + " could not be queued.",
        );
      }
      await Promise.all([
        refreshStage2Jobs(),
        refreshSourcePlans(),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue Stage 2 generation.");
    } finally {
      setCreatingStage2PlanIds(current => {
        const next = new Set(current);
        next.delete(plan.id);
        return next;
      });
    }
  }

  async function syncDriveSourcePlans() {
    if (syncingSourcePlans) return;
    setSyncingSourcePlans(true);
    setSourcePlanMessage("");
    setError("");
    try {
      const result = await syncSourcePlans();
      setSourcePlanMessage(
        "Scanned " + result.folders_scanned + " folders / " + result.images_found + " images. "
        + result.jobs_queued + " source plans queued for AI context analysis"
        + (result.plans_missing ? "; " + result.plans_missing + " removed sources archived" : "")
        + "; current target " + result.target_count + " refs per source.",
      );
      await refreshSourcePlans();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to scan the embroidery source folder.");
    } finally {
      setSyncingSourcePlans(false);
    }
  }

  async function setSourceReferenceFeedback(
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
    requestedLabel: ReferenceManualLabel,
  ) {
    if (!plan.campaign_id || reviewingReferenceIds.has(reference.id)) return;
    const nextLabel = (
      (requestedLabel === "good" && reference.picked)
      || (requestedLabel === "bad" && reference.rejected)
    ) ? "clear" : requestedLabel;
    const nextPicked = nextLabel === "good";
    const nextRejected = nextLabel === "bad" || nextLabel === "ai";
    const removeFromPicker = nextLabel === "bad" || nextLabel === "ai";

    setReviewingReferenceIds(current => new Set(current).add(reference.id));
    setError("");
    setSourcePage(current => ({
      ...current,
      items: current.items.map(item => item.id !== plan.id ? item : {
        ...item,
        reference_previews: removeFromPicker
          ? item.reference_previews.filter(row => row.id !== reference.id)
          : item.reference_previews.map(row => (
            row.id === reference.id
              ? { ...row, picked: nextPicked, rejected: nextRejected }
              : row
          )),
      }),
    }));
    try {
      await markCandidateReferenceFeedback(
        plan.campaign_id,
        reference.id,
        nextLabel,
        nextLabel === "good"
          ? "Marked suitable as a preferred reference from source row " + plan.source_relative_path
          : nextLabel === "bad"
            ? "Marked unsuitable and unusable from source row " + plan.source_relative_path
            : "Cleared explicit reference feedback for source row " + plan.source_relative_path,
      );
      setSourcePlanMessage(
        nextLabel === "good"
          ? "Reference marked suitable. Future Scout runs will learn from this positive example."
          : nextLabel === "ai"
            ? "Reference marked AI-generated. It was removed from real refs and will train both AI detection and negative reference preference."
            : nextLabel === "bad"
              ? "Reference marked unsuitable. It was removed from the picker and will train negative preference."
              : "Reference feedback cleared. Unreviewed references remain usable by default without a strong training signal.",
      );
      await refreshSourcePlans();
    } catch (reason) {
      setSourcePage(current => ({
        ...current,
        items: current.items.map(item => item.id !== plan.id ? item : plan),
      }));
      setError(reason instanceof Error ? reason.message : "Unable to update reference feedback.");
    } finally {
      setReviewingReferenceIds(current => {
        const next = new Set(current);
        next.delete(reference.id);
        return next;
      });
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSourcePageNumber(1);
      setDebouncedSourceQuery(sourceQuery.trim());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [sourceQuery]);

  useEffect(() => {
    if (sourcePageNumber > sourcePageCount) {
      setSourcePageNumber(sourcePageCount);
    }
  }, [sourcePageCount, sourcePageNumber]);

  useEffect(() => {
    if (!groupsStageActive) return;
    const controller = new AbortController();
    let refreshInFlight = true;
    setError("");
    if (sourcePage.items.length === 0 && sourcePage.total === 0) {
      setSourcePageLoading(true);
    }
    void refreshSourcePlans(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load source plans.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
        if (!controller.signal.aborted) setSourcePageLoading(false);
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshSourcePlans()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [groupsStageActive, sourcePageNumber, sourcePageSize, debouncedSourceQuery, sourceSortBy, sourceSortDirection]);

  useEffect(() => {
    if (activeStage !== "stage2") return;
    const controller = new AbortController();
    let refreshInFlight = true;
    void refreshStage2Jobs(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load Stage 2 jobs.");
        }
      })
      .finally(() => {
        refreshInFlight = false;
      });
    const timer = window.setInterval(() => {
      if (document.hidden || refreshInFlight) return;
      refreshInFlight = true;
      void refreshStage2Jobs()
        .catch(() => undefined)
        .finally(() => {
          refreshInFlight = false;
        });
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeStage]);

  function selectStage(nextStage: RrugcStageTab) {
    setActiveStage(nextStage);
    window.requestAnimationFrame?.(() => {
      document.getElementById("rrugc-tab-" + nextStage)?.focus();
    });
  }

  function handleStageTabsKeyDown(event: KeyboardEvent<HTMLElement>) {
    const currentIndex = RRUGC_STAGE_TABS.findIndex(item => item.id === activeStage);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % RRUGC_STAGE_TABS.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + RRUGC_STAGE_TABS.length) % RRUGC_STAGE_TABS.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = RRUGC_STAGE_TABS.length - 1;
    else return;
    event.preventDefault();
    selectStage(RRUGC_STAGE_TABS[nextIndex].id);
  }

  return <main className="rrugc-shell rrugc-source-first-shell">
    <aside className="ops-sidebar">
      <div className="brand"><b><BrandIcon /></b><span><strong>Creative assets</strong><small>UGC reference automation</small></span></div>
      <WorkspaceNavigation active="realistic-review-ugc" />
    </aside>
    <section className="rrugc-main">
      <WorkspacePageHeader
        className="rrugc-header"
        route="realistic-review-ugc"
        description="Automatically turn new Drive embroidery images into context-matched Pinterest reference sets."
        titleAddon={<span className="rrugc-page-live-pill"><i aria-hidden="true" />Source auto scan</span>}
        actions={<WorkspaceBackToAssets />}
      />
      <div className="rrugc-stage-tabs-shell">
        <nav
          className="ops-tabs rrugc-stage-tabs"
          aria-label="Realistic Review UGC sections"
          role="tablist"
          onKeyDown={handleStageTabsKeyDown}
        >
          {RRUGC_STAGE_TABS.map(item => (
            <button
              key={item.id}
              id={"rrugc-tab-" + item.id}
              type="button"
              role="tab"
              aria-selected={activeStage === item.id}
              aria-controls={"rrugc-panel-" + item.id}
              tabIndex={activeStage === item.id ? 0 : -1}
              className={activeStage === item.id ? "active" : ""}
              onClick={() => selectStage(item.id)}
            >
              <span className="rrugc-stage-tab-number" aria-hidden="true">{item.marker}</span>
              <span className="rrugc-stage-tab-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
            </button>
          ))}
        </nav>
      </div>

      <div className="rrugc-page-body rrugc-source-first-body">
        {error && <div className="rrugc-error" role="alert">{error}</div>}

        <section
          id="rrugc-panel-stage1"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage1"
          tabIndex={activeStage === "stage1" ? 0 : -1}
          hidden={activeStage !== "stage1"}
        >
          <SourcePlanTable
            plans={sourcePage.items}
            total={sourcePage.total}
            overview={sourcePage.overview}
            page={sourcePageNumber}
            pageSize={sourcePageSize}
            query={sourceQuery}
            sortBy={sourceSortBy}
            sortDirection={sourceSortDirection}
            syncing={syncingSourcePlans}
            loading={sourcePageLoading}
            reviewingReferenceIds={reviewingReferenceIds}
            message={sourcePlanMessage}
            onSync={() => void syncDriveSourcePlans()}
            onPageChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(value);
            }}
            onPageSizeChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourcePageSize(value);
            }}
            onQueryChange={setSourceQuery}
            onSortByChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourceSortBy(value);
            }}
            onSortDirectionChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourceSortDirection(value);
            }}
            onSetReferenceFeedback={(plan, reference, label) => void setSourceReferenceFeedback(plan, reference, label)}
          />
        </section>

        <section
          id="rrugc-panel-stage2"
          className="rrugc-stage-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-stage2"
          tabIndex={activeStage === "stage2" ? 0 : -1}
          hidden={activeStage !== "stage2"}
        >
          <Stage2JobTable
            plans={sourcePage.items}
            total={sourcePage.total}
            overview={sourcePage.overview}
            page={sourcePageNumber}
            pageSize={sourcePageSize}
            jobs={stage2Jobs}
            creatingPlanIds={creatingStage2PlanIds}
            loading={sourcePageLoading}
            message={stage2Message}
            onPageChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(value);
            }}
            onPageSizeChange={value => {
              setSourcePageLoading(true);
              setSourcePageNumber(1);
              setSourcePageSize(value);
            }}
            onCreateJob={(plan, candidateIds, skill) => void queueStage2Job(plan, candidateIds, skill)}
          />
        </section>

        <section
          id="rrugc-panel-settings"
          className="rrugc-stage-panel rrugc-settings-panel"
          role="tabpanel"
          aria-labelledby="rrugc-tab-settings"
          tabIndex={activeStage === "settings" ? 0 : -1}
          hidden={activeStage !== "settings"}
        >
          <div id="rrugc-scout" className="rrugc-anchor-section rrugc-source-scout-panel">
            <PinterestAutoScoutPanel onError={setError} />
          </div>
        </section>
      </div>
    </section>
  </main>;
}
