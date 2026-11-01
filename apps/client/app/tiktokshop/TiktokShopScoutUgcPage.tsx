import { useCallback, useEffect, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import { SourcePlanTable } from "../realistic-review-ugc/SourcePlanTable";
import { Stage3ReviewGroups } from "../realistic-review-ugc/Stage3ReviewGroups";
import { RrugcStageHeader } from "../realistic-review-ugc/RrugcStageHeader";
import {
  listSourcePlanFolders,
  listSourcePlans,
  syncSourcePlans,
  type SourcePlanFolders,
} from "../realistic-review-ugc/api";
import type { SourcePlanPage, Stage3ReviewGroupList } from "../realistic-review-ugc/types";
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
 * Phase 1: server-enforced Drive-root isolation and shared Stage 3/5 UI.
 * Generation and Scout claims are intentionally held until the backend assigns
 * agents/skills per workspace; NEVER show tenant-wide GatorHats jobs here.
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
        + " updated. Agent claims and generation are disabled until workspace isolation is complete.",
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
        titleAddon={<span className="rrugc-page-live-pill">Isolated Drive root · Phase 1</span>}
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
          <div className="rrugc-card" style={{ padding: "12px 16px", marginBottom: 12 }}>
            <strong>Source scan only.</strong> Google Drive sources are scoped to TiktokShop.
            Automatic Pinterest Scout and AI analysis are held until a dedicated
            Windows agent can claim only TikTokShop campaigns.
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
          <div className="rrugc-card" style={{ padding: 20 }}>
            <RrugcStageHeader
              kicker="STAGE 4 · PINTEREST → IMAGE EDIT"
              title="Image Generation"
              description="This tab will reuse GatorHats Stage 4's grid and job controls with a TiktokShop product-aware skill and isolated jobs."
            />
            <p>Generation is not enabled yet. The GatorHats 8869 skill
              must not run on TikTokShop apparel designs.</p>
          </div>
        </section>
        <section id="tiktok-panel-stage5" role="tabpanel" aria-labelledby="tiktok-tab-stage5"
          className="rrugc-stage-panel" hidden={stage !== "stage5"}>
          <div className="rrugc-card" style={{ padding: "12px 16px", marginBottom: 12 }}>
            Stage 5 review controls are reserved for TikTokShop outputs only.
            GatorHats results and Loox export are intentionally excluded.
          </div>
          <Stage3ReviewGroups
            data={EMPTY_REVIEWS}
            loading={false}
            analyzing={false}
            message=""
            onAnalyze={() => undefined}
          />
        </section>
      </div>
    </section>
  </main>;
}
