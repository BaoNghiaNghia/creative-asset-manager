import { useEffect, useMemo, useState } from "react";
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
import type { ReferenceManualLabel, SourcePlan, SourcePlanPage, SourcePlanReferencePreview, Stage2Job } from "./types";
import "./ui-overhaul.css";
import "./tablet-mobile-density.css";

const EMPTY_SOURCE_PAGE: SourcePlanPage = {
  items: [],
  page: 1,
  page_size: 20,
  total: 0,
};

export function RealisticReviewUgcPage() {
  const [sourcePage, setSourcePage] = useState<SourcePlanPage>(EMPTY_SOURCE_PAGE);
  const [sourcePageNumber, setSourcePageNumber] = useState(1);
  const [sourcePageSize, setSourcePageSize] = useState(20);
  const [sourceQuery, setSourceQuery] = useState("");
  const [debouncedSourceQuery, setDebouncedSourceQuery] = useState("");
  const [sourceSortBy, setSourceSortBy] = useState<SourcePlanSortBy>("source");
  const [sourceSortDirection, setSourceSortDirection] = useState<SourcePlanSortDirection>("asc");
  const [syncingSourcePlans, setSyncingSourcePlans] = useState(false);
  const [reviewingReferenceIds, setReviewingReferenceIds] = useState<Set<string>>(new Set());
  const [sourcePlanMessage, setSourcePlanMessage] = useState("");
  const [stage2Jobs, setStage2Jobs] = useState<Stage2Job[]>([]);
  const [creatingStage2PlanIds, setCreatingStage2PlanIds] = useState<Set<string>>(new Set());
  const [stage2Message, setStage2Message] = useState("");
  const [error, setError] = useState("");

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
    setSourcePage(result);
  }

  async function refreshStage2Jobs(signal?: AbortSignal) {
    setStage2Jobs(await listStage2Jobs(undefined, signal));
  }

  async function queueStage2Job(plan: SourcePlan, candidateIds: string[]) {
    if (creatingStage2PlanIds.has(plan.id) || candidateIds.length === 0) return;
    setCreatingStage2PlanIds(current => new Set(current).add(plan.id));
    setStage2Message("");
    setError("");
    try {
      const result = await createStage2Job(plan.id, candidateIds);
      setStage2Message(
        result.created
          ? "Stage 2 job queued with " + candidateIds.length + " Pinterest refs. The master will appear when the skill finishes."
          : "An identical Stage 2 job already exists; showing its current status.",
      );
      await refreshStage2Jobs();
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
    const controller = new AbortController();
    setError("");
    void refreshSourcePlans(controller.signal).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Unable to load source plans.");
      }
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshSourcePlans().catch(() => undefined);
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [sourcePageNumber, sourcePageSize, debouncedSourceQuery, sourceSortBy, sourceSortDirection]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshStage2Jobs(controller.signal).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Unable to load Stage 2 jobs.");
      }
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshStage2Jobs().catch(() => undefined);
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

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
      <div className="rrugc-page-body rrugc-source-first-body">
        {error && <div className="rrugc-error" role="alert">{error}</div>}

        <div id="rrugc-scout" className="rrugc-anchor-section rrugc-source-scout-panel">
          <PinterestAutoScoutPanel onError={setError} />
        </div>

        <SourcePlanTable
          plans={sourcePage.items}
          total={sourcePage.total}
          page={sourcePageNumber}
          pageSize={sourcePageSize}
          query={sourceQuery}
          sortBy={sourceSortBy}
          sortDirection={sourceSortDirection}
          syncing={syncingSourcePlans}
          reviewingReferenceIds={reviewingReferenceIds}
          message={sourcePlanMessage}
          onSync={() => void syncDriveSourcePlans()}
          onPageChange={setSourcePageNumber}
          onPageSizeChange={value => {
            setSourcePageNumber(1);
            setSourcePageSize(value);
          }}
          onQueryChange={setSourceQuery}
          onSortByChange={value => {
            setSourcePageNumber(1);
            setSourceSortBy(value);
          }}
          onSortDirectionChange={value => {
            setSourcePageNumber(1);
            setSourceSortDirection(value);
          }}
          onSetReferenceFeedback={(plan, reference, label) => void setSourceReferenceFeedback(plan, reference, label)}
        />

        <Stage2JobTable
          plans={sourcePage.items}
          jobs={stage2Jobs}
          creatingPlanIds={creatingStage2PlanIds}
          message={stage2Message}
          onCreateJob={(plan, candidateIds) => void queueStage2Job(plan, candidateIds)}
        />
      </div>
    </section>
  </main>;
}
