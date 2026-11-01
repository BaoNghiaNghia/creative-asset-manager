import { useCallback, useEffect, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import { SourcePlanTable } from "../realistic-review-ugc/SourcePlanTable";
import { Stage3ReviewGroups } from "../realistic-review-ugc/Stage3ReviewGroups";
import { TiktokShopAgentPanel } from "./TiktokShopAgentPanel";
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
  regenerateStage4Job,
  retryStage4Job,
  syncSourcePlans,
  type SourcePlanFolders,
} from "../realistic-review-ugc/api";
import type { SourcePlan, SourcePlanPage, Stage2Job, Stage3ReviewGroupList } from "../realistic-review-ugc/types";
import "../realistic-review-ugc/ui-overhaul.css";
import "../realistic-review-ugc/tablet-mobile-density.css";

export const TIKTOKSHOP_SOURCE_ROOT = "1LS8_v9HL-OmucwRFLcFwRU7iW5r3xJkh";
type TikTokStage = "stage3" | "stage4" | "stage5";
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
  const [stage, setStage] = useState<TikTokStage>("stage3");
  const [pageNumber, setPageNumber] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [query, setQuery] = useState("");
  const [folderPath, setFolderPath] = useState<string | null>(null);
  const [page, setPage] = useState<SourcePlanPage>(EMPTY_PAGE);
  const [folders, setFolders] = useState<SourcePlanFolders | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
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
        query, folderPath, sortBy: "source", sortDirection: "asc",
      }, signal),
      listSourcePlanFolders(signal, "tiktokshop"),
    ]);
    if (signal?.aborted) return;
    setPage(result);
    setFolders(folderResult);
  }, [pageNumber, pageSize, query, folderPath]);

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

  return <main className="rrugc-shell rrugc-source-first-shell">
    <aside className="ops-sidebar">
      <div className="brand">
        <b><BrandIcon /></b>
        <span><strong>Creative assets</strong><small>UGC reference automation</small></span>
      </div>
      <WorkspaceNavigation active="tiktokshop" />
    </aside>
    <section className="rrugc-main">
      <WorkspacePageHeader
        route="tiktokshop"
        titleAddon={<span className="rrugc-page-live-pill">TikTokShop · isolated Scout pilot</span>}
        actions={<div className="rrugc-global-management-actions">
          <a className="rrugc-global-management-button"
            href={"https://drive.google.com/drive/folders/" + TIKTOKSHOP_SOURCE_ROOT}
            target="_blank" rel="noreferrer">TiktokShop Drive</a>
          <WorkspaceBackToAssets />
        </div>}
      />
      <div className="rrugc-workflow-navigation">
        <nav className="ops-tabs rrugc-stage-tabs" aria-label="Tiktokshop Scout and UGC stages" role="tablist">
          {STAGES.map(item => <button key={item.id} type="button" role="tab"
            id={"tiktok-tab-" + item.id}
            aria-controls={"tiktok-panel-" + item.id}
            aria-selected={stage === item.id}
            tabIndex={stage === item.id ? 0 : -1}
            className={stage === item.id ? "active" : ""}
            onClick={() => setStage(item.id)}>
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
          <TiktokShopAgentPanel />
          <div className="rrugc-card" style={{ padding: "12px 16px", marginBottom: 12 }}>
            <strong>Stage 3 pilot.</strong> Scan imports only TiktokShop Drive sources
            and queues at most 12 product-context analyses each time.
            A paired TikTokShop Windows Agent receives only its own campaigns.
          </div>
          <SourcePlanTable
            rootFolderId={TIKTOKSHOP_SOURCE_ROOT}
            scoutEnabled={false}
            plans={page.items}
            folderData={folders}
            selectedFolderPath={folderPath}
            onFolderSelect={path => { setFolderPath(path); setPageNumber(1); }}
            total={page.total}
            overview={page.overview}
            page={pageNumber}
            pageSize={pageSize}
            sortBy="source"
            sortDirection="asc"
            query={query}
            onQueryChange={value => { setQuery(value); setPageNumber(1); }}
            syncing={syncing}
            loading={loading}
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
          <div className="rrugc-card" style={{ padding: "12px 16px", marginBottom: 12, display: "flex",
            gap: 12, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
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
  </main>;
}
