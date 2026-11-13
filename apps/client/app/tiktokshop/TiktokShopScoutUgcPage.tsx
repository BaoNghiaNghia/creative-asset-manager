import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import { useActiveStageVisibility } from "../components/useActiveStageVisibility";
import { SourcePlanTable } from "../realistic-review-ugc/SourcePlanTable";
import { Stage3ReviewGroups } from "../realistic-review-ugc/Stage3ReviewGroups";
import { TiktokShopSettingsModal } from "./TiktokShopSettingsModal";
import { TiktokShopStage4Panel } from "./TiktokShopStage4Panel";
import {
  analyzeStage3ReviewGroups,
  createStage2Job,
  downloadStage5LooxReviewConcepts,
  hideStage5Review,
  listStage2Jobs,
  listStage3ReviewGroups,
  listSourcePlanFolders,
  listSourcePlans,
  markCandidateReferenceFeedback,
  regenerateStage4Job,
  retryStage4Job,
  syncSourcePlans,
  type SourcePlanFolders,
  type SourcePlanSortBy,
  type SourcePlanSortDirection,
} from "../realistic-review-ugc/api";
import type {
  ReferenceManualLabel, SourcePlan, SourcePlanPage, SourcePlanReferencePreview,
  Stage2Job, Stage3ReviewGroupList,
} from "../realistic-review-ugc/types";
import "../realistic-review-ugc/ui-overhaul.css";
import "../realistic-review-ugc/tablet-mobile-density.css";
import "./tiktokshop.css";

export const TIKTOKSHOP_SOURCE_ROOT = "1LS8_v9HL-OmucwRFLcFwRU7iW5r3xJkh";
export type TikTokStage = "stage3" | "stage4" | "stage5";
export function tiktokStageFromSearch(search: string): TikTokStage {
  const value = new URLSearchParams(search).get("stage");
  return value === "stage4" || value === "stage5" ? value : "stage3";
}
const STAGES: readonly { id: TikTokStage; title: string; description: string }[] = [
  { id: "stage3", title: "Stage 3", description: "Pinterest References" },
  { id: "stage4", title: "Stage 4", description: "Image Generation" },
  { id: "stage5", title: "Stage 5", description: "UGC Review" },
];
const EMPTY_PAGE: SourcePlanPage = {
  items: [], total: 0, page: 1, page_size: 20,
  overview: {
    embroidery_groups: 0, source_images: 0, working_groups: 0, refs_loaded: 0,
    stage2_groups: 0, stage2_source_images: 0, stage2_drive_ready_refs: 0,
    stage2_active_jobs: 0,
  },
};
const EMPTY_REVIEWS: Stage3ReviewGroupList = {
  items: [], total_groups: 0, total_images: 0, ready_images: 0,
  rejected_images: 0, analyzing_images: 0, pending_images: 0, error_images: 0,
};

/**
 * Phase 3: all Stage 4/5 reads and actions use server-enforced TikTokShop
 * source-root isolation; never load GatorHats tenant-wide jobs here.
 * Generated images are internal draft concepts, not real buyer testimonials.
 */
export function TiktokShopScoutUgcPage() {
  const [stage, setStage] = useState<TikTokStage>(() => tiktokStageFromSearch(typeof window === "undefined" ? "" : window.location.search));
  const stageNavRef = useActiveStageVisibility(stage);

  useEffect(() => {
    const onPopState = () => setStage(tiktokStageFromSearch(window.location.search));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function selectStage(next: TikTokStage) {
    setStage(next);
    const url = new URL(window.location.href);
    if (next === "stage3") url.searchParams.delete("stage");
    else url.searchParams.set("stage", next);
    if (url.search !== window.location.search) {
      window.history.pushState(window.history.state, "", url.pathname + url.search + url.hash);
    }
  }

  function onStageKeyDown(event: KeyboardEvent<HTMLElement>) {
    const index = STAGES.findIndex(item => item.id === stage);
    const next = event.key === "ArrowRight" ? (index + 1) % STAGES.length
      : event.key === "ArrowLeft" ? (index - 1 + STAGES.length) % STAGES.length
      : event.key === "Home" ? 0 : event.key === "End" ? STAGES.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    selectStage(STAGES[next].id);
    window.requestAnimationFrame(() => document.getElementById("tiktok-tab-" + STAGES[next].id)?.focus());
  }
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const closeSettings = useCallback(() => {
    setSettingsOpen(false);
    settingsButtonRef.current?.focus();
  }, []);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sortBy, setSortBy] = useState<SourcePlanSortBy>("source");
  const [sortDirection, setSortDirection] = useState<SourcePlanSortDirection>("asc");
  const [query, setQuery] = useState("");
  const [folderPath, setFolderPath] = useState<string | null>(null);
  const [page, setPage] = useState<SourcePlanPage>(EMPTY_PAGE);
  const [folders, setFolders] = useState<SourcePlanFolders | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const autoSourceImportAttempted = useRef(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reviewingReferenceIds, setReviewingReferenceIds] = useState<Set<string>>(new Set());
  const [jobs, setJobs] = useState<Stage2Job[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsBusy, setJobsBusy] = useState(false);
  const [reviews, setReviews] = useState<Stage3ReviewGroupList>(EMPTY_REVIEWS);
  const [reviewsLoading, setReviewsLoading] = useState(false);
  const [reviewAnalyzing, setReviewAnalyzing] = useState(false);
  const [reviewsMoreLoading, setReviewsMoreLoading] = useState(false);
  const [reviewMessage, setReviewMessage] = useState("");

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [result, folderResult] = await Promise.all([
      listSourcePlans({
        workspace: "tiktokshop", page: pageNumber, pageSize,
        query, folderPath, sortBy, sortDirection,
      }, signal),
      listSourcePlanFolders(signal, "tiktokshop"),
    ]);
    if (signal?.aborted) return;
    setPage(result);
    setFolders(folderResult);
    return result;
  }, [pageNumber, pageSize, query, folderPath, sortBy, sortDirection]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void refresh(controller.signal)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "TikTokShop source metadata unavailable.");
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  // Keep Scout progress live while Stage 3 is open. Drive source metadata is
  // already synchronized by the server, but previously the page fetched its
  // counts only once and appeared stuck even as new refs were imported.
  useEffect(() => {
    if (stage !== "stage3") return;
    let inFlight = false;
    let active = true;
    const controller = new AbortController();
    const timer = setInterval(() => {
      if (inFlight || syncing || !active || document.visibilityState === "hidden") return;
      inFlight = true;
      void refresh(controller.signal).catch(() => {
        // Retain last known counts on transient network failures.
      }).finally(() => { inFlight = false; });
    }, 20_000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [stage, syncing, refresh]);

  // A fresh TikTokShop workspace has no DB source plans until a Drive sync.
  // Import file metadata once when the unfiltered Stage 3 is first opened.
  // Never start Gemini, Pinterest Scout or image-generation jobs automatically.
  useEffect(() => {
    if (autoSourceImportAttempted.current || loading || syncing || error
      || page.total !== 0 || pageNumber !== 1 || query.trim() || folderPath) return;
    autoSourceImportAttempted.current = true;
    setSyncing(true);
    void (async () => {
      try {
        const result = await syncSourcePlans(false, "tiktokshop", true);
        setMessage("TikTokShop Drive: imported " + result.images_found
          + " source photos from " + result.folders_scanned
          + " folders. No AI analysis jobs were started.");
        await refresh();
      } catch (reason) {
        setError(reason instanceof Error
          ? "Cannot import TikTokShop Drive photos: " + reason.message
          : "Cannot import TikTokShop Drive photos. Check Managed Storage access.");
      } finally {
        setSyncing(false);
      }
    })();
  }, [loading, syncing, error, page.total, pageNumber, query, folderPath, refresh]);

  const refreshJobs = useCallback(async (signal?: AbortSignal) => {
    const result = await listStage2Jobs([], signal, "tiktokshop");
    if (!signal?.aborted) setJobs(result);
  }, []);

  const refreshReviews = useCallback(async (signal?: AbortSignal) => {
    const result = await listStage3ReviewGroups(signal, undefined, null, "tiktokshop");
    if (!signal?.aborted) setReviews(result);
  }, []);

  useEffect(() => {
    if (stage !== "stage4") return;
    const controller = new AbortController();
    setJobsLoading(true);
    void refreshJobs(controller.signal)
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason?.message || reason)); })
      .finally(() => { if (!controller.signal.aborted) setJobsLoading(false); });
    return () => controller.abort();
  }, [stage, refreshJobs]);

  useEffect(() => {
    if (stage !== "stage5") return;
    const controller = new AbortController();
    setReviewsLoading(true);
    void refreshReviews(controller.signal)
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason?.message || reason)); })
      .finally(() => { if (!controller.signal.aborted) setReviewsLoading(false); });
    return () => controller.abort();
  }, [stage, refreshReviews]);

  const queueGeneration = async (plan: SourcePlan, ids: string[]) => {
    setJobsBusy(true);
    setError("");
    try {
      const created = await createStage2Job(plan.id, ids, {
        source: "local", skill_id: null, skill_name: "tiktokshop-apparel-ugc-v1",
        skill_version: null,
      }, undefined, "tiktokshop");
      setMessage(created.created ? "TikTokShop apparel UGC draft queued." : "Existing TikTokShop job returned.");
      await refreshJobs();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "TikTokShop generation failed."); throw reason; }
    finally { setJobsBusy(false); }
  };

  const updateJob = async (job: Stage2Job, action: "retry" | "regenerate") => {
    setJobsBusy(true);
    setError("");
    try {
      await (action === "retry"
        ? retryStage4Job(job.id, "tiktokshop")
        : regenerateStage4Job(job.id, "tiktokshop"));
      await refreshJobs();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "TikTokShop job update failed."); throw reason; }
    finally { setJobsBusy(false); }
  };

  const analyzeReviews = async (folderId?: string) => {
    setReviewAnalyzing(true);
    setError("");
    try {
      const result = await analyzeStage3ReviewGroups(folderId, false, "tiktokshop");
      setReviewMessage(result.queued + " apparel draft QA jobs queued; " + result.remaining + " remaining.");
      await refreshReviews();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "TikTokShop draft QA failed."); }
    finally { setReviewAnalyzing(false); }
  };

  const hideReview = async (jobId: string) => {
    setError("");
    try {
      await hideStage5Review(jobId, "tiktokshop");
      await refreshReviews();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to hide draft."); throw reason; }
  };

  const loadMoreReviews = async () => {
    if (!reviews.next_cursor || reviewsMoreLoading) return;
    setReviewsMoreLoading(true);
    try {
      const result = await listStage3ReviewGroups(undefined, reviews.next_cursor, null, "tiktokshop");
      setReviews(current => {
        const combined = new Map(current.items.map(group => [group.folder_id, { ...group, images: [...group.images] }]));
        for (const group of result.items) {
          const existing = combined.get(group.folder_id);
          if (existing) {
            const seen = new Set(existing.images.map(image => image.stage2_job_id));
            existing.images.push(...group.images.filter(image => !seen.has(image.stage2_job_id)));
            existing.image_count = existing.images.length;
          } else combined.set(group.folder_id, group);
        }
        return {
          ...result,
          items: [...combined.values()],
          total_groups: combined.size,
          total_images: [...combined.values()].reduce((count, group) => count + group.images.length, 0),
        };
      });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load more TikTokShop drafts."); }
    finally { setReviewsMoreLoading(false); }
  };

  async function setReferenceFeedback(
    plan: SourcePlan,
    reference: SourcePlanReferencePreview,
    requestedLabel: ReferenceManualLabel | "clear",
  ) {
    if (!plan.campaign_id || reviewingReferenceIds.has(reference.id)) return;
    const sameLabel = (requestedLabel === "good" && reference.picked)
      || (requestedLabel === "bad" && (
        reference.manual_label === "bad" || (!reference.manual_label && reference.rejected)
      ))
      || (requestedLabel === "ai" && reference.manual_label === "ai");
    const label = sameLabel ? "clear" : requestedLabel;
    const picked = label === "good";
    const rejected = label === "bad" || label === "ai";
    setReviewingReferenceIds(previous => new Set(previous).add(reference.id));
    setError("");
    setPage(previous => ({
      ...previous,
      items: previous.items.map(item => item.id !== plan.id ? item : {
        ...item,
        reference_previews: item.reference_previews.map(row => (
          row.id === reference.id
            ? { ...row, picked, rejected, manual_label: label === "clear" ? null : label }
            : row
        )),
      }),
    }));
    try {
      await markCandidateReferenceFeedback(
        plan.campaign_id,
        reference.id,
        label,
        label === "good"
          ? "TikTokShop Stage 3: suitable apparel reference"
          : label === "bad"
            ? "TikTokShop Stage 3: unsuitable apparel reference"
            : label === "ai"
              ? "TikTokShop Stage 3: AI-generated reference"
              : "TikTokShop Stage 3: feedback cleared",
        "tiktokshop",
      );
      setMessage(label === "good"
        ? "Reference approved. Scout will prioritize similar suitable images."
        : label === "bad"
          ? "Reference rejected. It will be excluded from usable images."
          : label === "ai"
            ? "AI-generated reference flagged and excluded from real photo selections."
            : "Reference feedback cleared.");
      await refresh();
    } catch (reason) {
      setPage(previous => ({
        ...previous,
        items: previous.items.map(item => item.id !== plan.id ? item : {
          ...item,
          reference_previews: item.reference_previews.map(row =>
            row.id === reference.id ? reference : row),
        }),
      }));
      setError(reason instanceof Error ? reason.message : "Unable to save TikTokShop reference feedback.");
    } finally {
      setReviewingReferenceIds(previous => {
        const next = new Set(previous);
        next.delete(reference.id);
        return next;
      });
    }
  }

  async function scanDrive() {
    if (syncing) return;
    setSyncing(true);
    setError("");
    setMessage("");
    try {
      const result = await syncSourcePlans(false, "tiktokshop");
      setMessage(
        "TiktokShop Drive scan: " + result.images_found + " images, "
        + result.plans_created + " new, " + result.plans_updated
        + " updated; " + result.jobs_queued
        + " product-context analysis jobs queued (at most 12 per Scan). "
        + "Only paired TikTokShop Scouts can claim their Pinterest campaigns.",
      );
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "TikTokShop Drive scan failed.");
    } finally {
      setSyncing(false);
    }
  }

  return <main className="rrugc-shell rrugc-source-first-shell rrugc-tiktokshop-workspace cam-unified-workspace-v18">
    <aside className="ops-sidebar">
      <div className="brand">
        <b><BrandIcon /></b>
        <span><strong>Creative assets</strong><small>UGC reference automation</small></span>
      </div>
      <WorkspaceNavigation active="tiktokshop" />
    </aside>
    <section className="rrugc-main">
      <WorkspacePageHeader
        route="tiktokshop" className="cam-header-v18"
        titleAddon={<span className="rrugc-page-live-pill">TikTokShop · isolated Scout pilot</span>}
        actions={<div className="rrugc-global-management-actions">
          <button type="button" ref={settingsButtonRef}
            className="rrugc-global-management-button"
            aria-haspopup="dialog" aria-expanded={settingsOpen}
            onClick={() => setSettingsOpen(true)}
          ><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
              <circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="2"/>
            </svg>Settings</button>
          <a className="rrugc-global-management-button"
            href={"https://drive.google.com/drive/folders/" + TIKTOKSHOP_SOURCE_ROOT}
            target="_blank" rel="noreferrer">TiktokShop Drive</a>
          <WorkspaceBackToAssets />
        </div>}
      />
      <div className="rrugc-workflow-navigation">
        <nav ref={stageNavRef} className="ops-tabs rrugc-stage-tabs" aria-label="Tiktokshop Scout and UGC stages" role="tablist" onKeyDown={onStageKeyDown}>
          {STAGES.map(item => <button key={item.id} type="button" role="tab"
            id={"tiktok-tab-" + item.id}
            aria-controls={"tiktok-panel-" + item.id}
            aria-selected={stage === item.id}
            tabIndex={stage === item.id ? 0 : -1}
            className={stage === item.id ? "active" : ""}
            onClick={() => selectStage(item.id)}>
            <span className="rrugc-stage-tab-number" aria-hidden="true">{item.id.slice(-1)}</span>
            <span className="rrugc-stage-tab-copy">
              <strong>{item.title}</strong><small>{item.description}</small>
            </span>
          </button>)}
        </nav>
      </div>
      <div className="rrugc-page-body rrugc-source-first-body">
        {error && <div role="alert" className="rrugc-error">{error}</div>}
        <section id="tiktok-panel-stage3" role="tabpanel" aria-labelledby="tiktok-tab-stage3"
          className="rrugc-stage-panel" hidden={stage !== "stage3"}>
          <SourcePlanTable
            rootFolderId={TIKTOKSHOP_SOURCE_ROOT}
            scoutEnabled={page.items.some(item => item.auto_scout)}
            plans={page.items}
            folderData={folders}
            selectedFolderPath={folderPath}
            onFolderSelect={path => { setFolderPath(path); setPageNumber(1); }}
            total={page.total}
            overview={page.overview}
            page={pageNumber}
            pageSize={pageSize}
            sortBy={sortBy}
            sortDirection={sortDirection}
            onSortByChange={value => { setPageNumber(1); setSortBy(value); }}
            onSortDirectionChange={value => { setPageNumber(1); setSortDirection(value); }}
            query={query}
            onQueryChange={value => { setQuery(value); setPageNumber(1); }}
            syncing={syncing}
            loading={loading || syncing}
            reviewingReferenceIds={reviewingReferenceIds}
            onSetReferenceFeedback={(plan, reference, label) => void setReferenceFeedback(plan, reference, label)}
            message={message}
            onSync={() => void scanDrive()}
            onPageChange={setPageNumber}
            onPageSizeChange={value => { setPageSize(value); setPageNumber(1); }}
          />
        </section>
        <section id="tiktok-panel-stage4" role="tabpanel" aria-labelledby="tiktok-tab-stage4"
          className="rrugc-stage-panel" hidden={stage !== "stage4"}>
          <TiktokShopStage4Panel plans={page.items} jobs={jobs}
            loading={loading || jobsLoading} busy={jobsBusy}
            page={pageNumber} pageSize={pageSize} total={page.total}
            onPageChange={setPageNumber}
            onCreate={queueGeneration}
            onRetry={job => updateJob(job, "retry")}
            onRegenerate={job => updateJob(job, "regenerate")}
            onRefresh={() => void refreshJobs().catch(reason =>
              setError(reason instanceof Error ? reason.message : "Unable to refresh TikTokShop jobs."))}/>
        </section>
        <section id="tiktok-panel-stage5" role="tabpanel" aria-labelledby="tiktok-tab-stage5"
          className="rrugc-stage-panel" hidden={stage !== "stage5"}>
          <div className="rrugc-card tiktokshop-stage-note tiktokshop-stage-note-actions">
            <div><strong>Internal apparel image QA only</strong>
              <p style={{ margin: "4px 0" }}>TikTokShop outputs are creative drafts, not actual customer photographs or verified buyer reviews. No fake reviewer, rating or Loox import is generated.</p>
            </div>
            <button type="button" className="rrugc-icon-action"
              onClick={() => void downloadStage5LooxReviewConcepts("internal_csv", "tiktokshop")
                .catch(reason => setError(reason instanceof Error ? reason.message : "Unable to export internal QA drafts."))}>
              Export internal QA CSV
            </button>
          </div>
          <Stage3ReviewGroups
            data={reviews}
            loading={reviewsLoading}
            analyzing={reviewAnalyzing}
            message={reviewMessage}
            onAnalyze={folderId => { void analyzeReviews(folderId); }}
            onLoadMore={reviews.has_more ? () => void loadMoreReviews() : undefined}
            loadingMore={reviewsMoreLoading}
            onRefresh={() => void refreshReviews().catch(reason =>
              setError(reason instanceof Error ? reason.message : "Unable to refresh TikTokShop QA."))}
            onDeleteReview={hideReview}
          />
        </section>
      </div>
    </section>
    <TiktokShopSettingsModal open={settingsOpen} onClose={closeSettings} />
  </main>;
}
