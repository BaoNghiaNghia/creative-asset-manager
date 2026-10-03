import { useEffect, useMemo, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import { listSourcePlans, markCandidateReferenceFeedback, syncSourcePlans } from "./api";
import { PinterestAutoScoutPanel } from "./PinterestAutoScoutPanel";
import { SourcePlanTable } from "./SourcePlanTable";
import type { SourcePlan, SourcePlanPage, SourcePlanReferencePreview } from "./types";
import "./ui-overhaul.css";

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
  const [syncingSourcePlans, setSyncingSourcePlans] = useState(false);
  const [pickingReferenceIds, setPickingReferenceIds] = useState<Set<string>>(new Set());
  const [sourcePlanMessage, setSourcePlanMessage] = useState("");
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
      },
      signal,
    );
    setSourcePage(result);
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

  async function toggleSourceReferencePick(
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
  ) {
    if (!plan.campaign_id || pickingReferenceIds.has(reference.id)) return;
    const nextPicked = !reference.picked;
    setPickingReferenceIds(current => new Set(current).add(reference.id));
    setError("");
    setSourcePage(current => ({
      ...current,
      items: current.items.map(item => item.id !== plan.id ? item : {
        ...item,
        reference_previews: item.reference_previews.map(row => (
          row.id === reference.id ? { ...row, picked: nextPicked } : row
        )),
      }),
    }));
    try {
      await markCandidateReferenceFeedback(
        plan.campaign_id,
        reference.id,
        nextPicked ? "good" : "clear",
        nextPicked
          ? "Picked as a positive reference from source row " + plan.source_relative_path
          : "Removed from picked references for source row " + plan.source_relative_path,
      );
      setSourcePlanMessage(
        nextPicked
          ? "Reference picked. Future Scout runs for this source row will learn from this positive example."
          : "Reference unpicked. Its positive training signal was removed.",
      );
      await refreshSourcePlans();
    } catch (reason) {
      setSourcePage(current => ({
        ...current,
        items: current.items.map(item => item.id !== plan.id ? item : {
          ...item,
          reference_previews: item.reference_previews.map(row => (
            row.id === reference.id ? { ...row, picked: reference.picked } : row
          )),
        }),
      }));
      setError(reason instanceof Error ? reason.message : "Unable to update the picked reference.");
    } finally {
      setPickingReferenceIds(current => {
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
  }, [sourcePageNumber, sourcePageSize, debouncedSourceQuery]);

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
          syncing={syncingSourcePlans}
          pickingReferenceIds={pickingReferenceIds}
          message={sourcePlanMessage}
          onSync={() => void syncDriveSourcePlans()}
          onPageChange={setSourcePageNumber}
          onPageSizeChange={value => {
            setSourcePageNumber(1);
            setSourcePageSize(value);
          }}
          onQueryChange={setSourceQuery}
          onToggleReferencePick={(plan, reference) => void toggleSourceReferencePick(plan, reference)}
        />
      </div>
    </section>
  </main>;
}
