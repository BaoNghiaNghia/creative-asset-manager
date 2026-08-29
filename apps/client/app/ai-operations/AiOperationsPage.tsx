import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  aiOperationsExportUrl, cancelAiOperationsJob, fetchAiOperationsDashboard, filtersFromSearch, repairSearchCoverage, runSearchCoverageAudit,
  retryAiOperationsJob, retryAiOperationsJobsByError, searchFromFilters, fetchAiOperationsVideoDetail,
  type AiOpsDashboardData, type AiOpsFilters, type AiOpsJob, type AiOpsUsage, type AiOpsSearchCoverage, type PipelineSnapshot,
} from "../../features/ai_operations";
import { AccessibleChart } from "./AccessibleChart";
import { fetchAccessIdentity, type AccessIdentity } from "../../features/access_management";
import { ConfigurationTab, ProvidersTab } from "./ProvidersConfiguration";
import { InventoryDailyTab } from "./InventoryDailyTab";
import { AssetDetailsPanel } from "../components/AssetDetailsPanel";
import { BrandIcon } from "../components/Icons";
import type { Asset } from "../types";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
import { fetchAiOperationsConfiguration, setTenantAiPaused, setVideoAiPaused, type AiOpsConfiguration } from "../../features/ai_operations";
import {
  dailyProviderCostChart, dailyStatusChart, failureChart,
  formatCost, formatDuration, modeLabel, providerLabel, providerVolumeChart,
} from "./presentation";
import {
  AUTO_REFRESH_SECONDS, DashboardRequestCoordinator, autoRefreshFromSearch,
  shouldAutoRefresh, type AutoRefreshSeconds,
} from "./requestCoordinator";

export type AiOpsTab = "pipeline" | "overview" | "processing" | "inventory" | "cost" | "providers" | "configuration";
const tabs: Array<{ id: AiOpsTab; label: string; icon: TabIconName }> = [
  { id: "pipeline", label: "Pipeline overview", icon: "pipeline" },
  { id: "overview", label: "AI analysis", icon: "spark" },
  { id: "processing", label: "Processing", icon: "processing" },
  { id: "inventory", label: "Inventory Daily", icon: "inventory" },
  { id: "cost", label: "Cost & Usage", icon: "cost" },
  { id: "providers", label: "Providers", icon: "providers" },
  { id: "configuration", label: "Configuration", icon: "configuration" },
];
type TabIconName = "pipeline" | "spark" | "processing" | "inventory" | "cost" | "providers" | "configuration";

function TabIcon({ name }: { name: TabIconName }) {
  const paths: Record<TabIconName, string> = {
    pipeline: "M4 5h16M4 12h16M4 19h16",
    spark: "M12 3l1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3z",
    processing: "M6 4h12v16H6z M9 8h6M9 12h6M9 16h4",
    inventory: "M4 7l8-4 8 4-8 4-8-4z M4 12l8 4 8-4 M4 17l8 4 8-4",
    cost: "M12 3v18 M16 7.5c0-1.7-1.8-3-4-3s-4 1.3-4 3c0 1.7 1.8 3 4 3s4 1.3 4 3c0 1.7-1.8 3-4 3s-4-1.3-4-3",
    providers: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z M4 7.5l8 4.5 8-4.5 M12 12v9",
    configuration: "M4 7h10M18 7h2M4 17h2M10 17h10 M14 5v4M8 15v4",
  };
  return <svg className="ops-tab-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} /></svg>;
}

const emptyPage = <T,>(page = 1) => ({ page, page_size: 25, total: 0, items: [] as T[] });
export const emptyDashboard = (page = 1): AiOpsDashboardData => ({
  summary: null, today: null, month: null, daily: [], providers: [], todayProviders: [], failures: [],
  jobs: emptyPage<AiOpsJob>(page), usage: emptyPage<AiOpsUsage>(), coverage: null, pipeline: null, media: null,
});

export function AiOperationsPage() {
  const [filters, setFilters] = useState(() => filtersFromSearch(window.location.search));
  const initialTab = new URLSearchParams(window.location.search).get("tab") as AiOpsTab | null;
  const [tab, setTab] = useState<AiOpsTab>(tabs.some(item => item.id === initialTab) ? initialTab! : "overview");
  const [media, setMedia] = useState<"image" | "video">(() => new URLSearchParams(window.location.search).get("media") === "video" ? "video" : "image");
  const [refreshSeconds, setRefreshSeconds] = useState<AutoRefreshSeconds>(() => autoRefreshFromSearch(window.location.search));
  const [data, setData] = useState<AiOpsDashboardData>(() => emptyDashboard(filters.page));
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [unauthorized, setUnauthorized] = useState(false);
  const [identity, setIdentity] = useState<AccessIdentity | null>(null);
  const [authorizationReason, setAuthorizationReason] = useState("Sign in is required.");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [reload, setReload] = useState(0);
  const [detailsAssetId, setDetailsAssetId] = useState<string | null>(null);
  const [detailsVideo, setDetailsVideo] = useState<{ item: Asset; analysis: VideoSearchItem } | null>(null);
  const requests = useRef(new DashboardRequestCoordinator());

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity().then(value => {
      if (!alive) return;
      setIdentity(value);
      if (!value.permissions.includes("ai_operations.read")) {
        setUnauthorized(true);
        setAuthorizationReason("Missing permission: ai_operations.read");
      }
    }).catch(error => {
      if (!alive) return;
      const status = typeof error === "object" && error && "status" in error ? Number(error.status) : 0;
      if (status === 401) {
        setUnauthorized(true);
        setAuthorizationReason("Sign in is required.");
      } else if (status === 403) {
        setUnauthorized(true);
        setAuthorizationReason("Missing permission: ai_operations.read");
      }
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let subscribed = true;
    setLoading(true);
    requests.current.run(signal => fetchAiOperationsDashboard(
      filters, fetch, undefined, signal,
    )).then(result => {
      if (!subscribed || !result.current) return;
      if ("value" in result) {
        setData(result.value.data);
        setErrors(result.value.errors);
        setUnauthorized(result.value.unauthorized);
        setLastUpdated(new Date());
      } else {
        setErrors(["Dashboard request failed. Try again."]);
      }
      setLoading(false);
    });
    return () => {
      subscribed = false;
      requests.current.abort();
    };
  }, [filters, reload]);

  useEffect(() => {
    if (!refreshSeconds) return;
    const timer = window.setInterval(() => {
      if (shouldAutoRefresh(refreshSeconds, document.visibilityState)) {
        setReload(value => value + 1);
      }
    }, refreshSeconds * 1000);
    return () => window.clearInterval(timer);
  }, [refreshSeconds]);

  function changeFilters(next: AiOpsFilters) {
    setFilters(next);
    updateUrl(next, tab, refreshSeconds, media);
  }
  function changeTab(next: AiOpsTab) {
    setTab(next);
    updateUrl(filters, next, refreshSeconds, media);
  }
  function changeRefresh(next: AutoRefreshSeconds) {
    setRefreshSeconds(next);
    updateUrl(filters, tab, next, media);
  }
  function changeMedia(next: "image" | "video") {
    setMedia(next);
    updateUrl(filters, tab, refreshSeconds, next);
  }
  async function openVideoDetails(sourceAssetId: string) {
    try {
      const detail = await fetchAiOperationsVideoDetail(sourceAssetId);
      setDetailsAssetId(null);
      setDetailsVideo({
        item: {
          provider: detail.source_type?.includes("sharepoint") ? "sharepoint" : "google-drive",
          id: detail.external_asset_id || detail.source_asset_id,
          name: detail.filename,
          kind: "video",
          mime_type: detail.mime_type,
          size: detail.size_bytes ?? undefined,
          modified_at: detail.modified_at ?? undefined,
          thumbnail_url: detail.thumbnail_url ?? undefined,
          web_url: detail.web_url ?? undefined,
          source_asset_id: detail.source_asset_id,
          external_source_id: detail.external_source_id ?? undefined,
          folder_path: detail.location ?? undefined,
        },
        analysis: detail,
      });
    } catch (error) {
      setErrors(current => [...current, error instanceof Error ? error.message : "Unable to load video details."]);
    }
  }
  return <AiOperationsShell>
    <AiOperationsContent
      data={data} loading={loading} errors={errors} unauthorized={unauthorized}
      filters={filters} tab={tab} onTab={changeTab} onFilters={changeFilters}
      refreshSeconds={refreshSeconds} onRefreshSeconds={changeRefresh}
      media={media} onMedia={changeMedia}
      lastUpdated={lastUpdated}
      permissions={identity?.permissions || []}
      authorizationReason={authorizationReason}
      onRetry={() => setReload(value => value + 1)}
      onOpenAsset={assetId => { setDetailsVideo(null); setDetailsAssetId(assetId); }}
      onOpenVideo={sourceAssetId => void openVideoDetails(sourceAssetId)}
    />
    {detailsVideo ? <AssetDetailsPanel item={detailsVideo.item} videoAnalysis={detailsVideo.analysis} onClose={() => setDetailsVideo(null)} /> : null}
    {!detailsVideo && detailsAssetId ? <AssetDetailsPanel item={null} assetId={detailsAssetId} onClose={() => setDetailsAssetId(null)} /> : null}
  </AiOperationsShell>;
}

function updateUrl(filters: AiOpsFilters, tab: AiOpsTab, refreshSeconds: AutoRefreshSeconds, media: "image" | "video" = "image") {
  const query = searchFromFilters(filters, tab, refreshSeconds, media);
  window.history.replaceState({}, "", `/ai-operations${query ? `?${query}` : ""}`);
}

export function AiOperationsShell({ children }: { children: React.ReactNode }) {
  return <main className="ops-shell">
    <aside className="ops-sidebar">
      <div className="brand"><b><BrandIcon /></b><span><strong>Creative assets</strong><small>Operations console</small></span></div>
      <p>WORKSPACE</p>
      <a href="/">▧ Asset Explorer</a>
      <a href="/ai-operations" className="active" aria-current="page">◉ AI Operations</a>
      <a href="/settings/access">⚿ Access Management</a>
      <small className="ops-sidebar-note">Tenant-scoped metrics. Provider secrets are never shown.</small>
    </aside>
    <section className="ops-main">{children}</section>
  </main>;
}

type ContentProps = {
  data: AiOpsDashboardData;
  filters: AiOpsFilters;
  tab: AiOpsTab;
  loading?: boolean;
  errors?: string[];
  unauthorized?: boolean;
  onTab: (tab: AiOpsTab) => void;
  onFilters: (filters: AiOpsFilters) => void;
  onRetry: () => void;
  refreshSeconds?: AutoRefreshSeconds;
  onRefreshSeconds?: (seconds: AutoRefreshSeconds) => void;
  lastUpdated?: Date | null;
  permissions?: string[];
  authorizationReason?: string;
  media?: "image" | "video";
  onMedia?: (media: "image" | "video") => void;
  onOpenAsset?: (assetId: string) => void;
  onOpenVideo?: (sourceAssetId: string) => void;
};

export function AiOperationsContent({
  data, filters, tab, loading = false, errors = [], unauthorized = false,
  onTab, onFilters, onRetry, refreshSeconds = 0, onRefreshSeconds = () => undefined,
  lastUpdated = null, permissions = [], authorizationReason = "Sign in is required.", media = "image", onMedia = () => undefined,
  onOpenAsset = () => undefined, onOpenVideo = () => undefined,
}: ContentProps) {
  const models = useMemo(() => [...new Set([
    ...data.providers.map(item => item.model || ""), ...data.usage.items.map(item => item.model || ""),
  ].filter(Boolean))].sort(), [data]);
  const profiles = useMemo(() => [...new Set(data.usage.items.map(item => item.metadata_profile || "").filter(Boolean))].sort(), [data]);
  const hasMediaTabs = tab === "pipeline" || tab === "overview" || tab === "processing";
  if (unauthorized) return <DashboardState kind="unauthorized" label={authorizationReason} onRetry={onRetry} />;
  return <>
    <header className="ops-header">
      <div><small>OPERATIONS</small><h1>Processing Operations</h1><p>Pipeline progress, AI analysis, usage and cost for the current tenant.</p></div>
      <div className="ops-header-actions">
        <AiWorkerToggle workers={data.media?.workers ?? []} />
        <label className="ops-refresh-control"><span>Auto-refresh</span><select aria-label="Auto-refresh interval" value={refreshSeconds} onChange={event => onRefreshSeconds(Number(event.target.value) as AutoRefreshSeconds)}>
          {AUTO_REFRESH_SECONDS.map(seconds => <option key={seconds} value={seconds}>{seconds ? `${seconds}s` : "Off"}</option>)}
        </select></label>
        <div className="ops-refresh-status" aria-live="polite">
          <span className="ops-refresh-status-label">{loading ? "Refreshing dashboard" : lastUpdated ? "Last updated" : "Refresh status"}</span>
          {lastUpdated ? <time dateTime={lastUpdated.toISOString()}>{loading ? "Refreshing..." : lastUpdated.toLocaleTimeString()}</time> : <span className="ops-refresh-status-value">Manual refresh</span>}
        </div>
        <a className="ops-back-link" href="/">← Back to assets</a>
      </div>
    </header>
    <nav className="ops-tabs" aria-label="Processing Operations sections" role="tablist" onKeyDown={event => handleTabKeyDown(event, tab, onTab)}>
      {tabs.map(item => <button key={item.id} id={`ops-tab-${item.id}`} type="button" role="tab" aria-selected={tab === item.id} aria-controls={`ops-panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1} className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}><TabIcon name={item.icon} /><span>{item.label}</span></button>)}
    </nav>
    {tab !== "inventory" && <div className="ops-query-bar">
      {hasMediaTabs && <MediaTypeTabs media={media} onMedia={onMedia} label={tab === "pipeline" ? "Pipeline media type" : tab === "processing" ? "Processing media type" : "AI analysis media type"} />}
      <AiOperationsFilters filters={filters} models={models} profiles={profiles} onChange={onFilters} />
      <details className="ops-export-menu">
        <summary>Export data</summary>
        <nav aria-label="AI Operations CSV exports">
          {(["daily", "usage", "failures", "jobs"] as const).map(kind => <a key={kind} href={aiOperationsExportUrl(kind, filters)}>Export {kind} CSV</a>)}
        </nav>
      </details>
    </div>}
    {tab !== "inventory" && errors.length > 0 && <div className="ops-partial-error" role="alert" aria-live="assertive">
      <div><b>Some dashboard data could not be loaded.</b><span>{errors.join(" · ")}</span></div>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>}
    <section id={`ops-panel-${tab}`} role="tabpanel" aria-labelledby={`ops-tab-${tab}`} tabIndex={0}>
      {tab === "inventory" ? <InventoryDailyTab /> : loading ? <DashboardSkeleton /> : tab === "pipeline" ? <PipelineOverview pipeline={data.pipeline} mediaDashboard={data.media} media={media} onMedia={onMedia} onOpenAsset={onOpenAsset} onOpenVideo={onOpenVideo} onPage={(page, pageSize) => onFilters({ ...filters, pipelinePage: page, pipelinePageSize: pageSize })} onVideoPage={(page, pageSize) => onFilters({ ...filters, videoPage: page, videoPageSize: pageSize })} />
        : tab === "overview" ? <Overview data={data} media={media} onMedia={onMedia} canManage={permissions.includes("search.rebuild")} onRefresh={onRetry} />
        : tab === "processing" ? <Processing data={data} filters={filters} permissions={permissions} onFilters={onFilters} onActionAccepted={onRetry} onOpenAsset={onOpenAsset} onOpenVideo={onOpenVideo} media={media} onVideoPage={(page, pageSize) => onFilters({ ...filters, videoPage: page, videoPageSize: pageSize })} />
        : tab === "cost" ? <CostUsage data={data} filters={filters} onFilters={onFilters} />
        : tab === "providers" ? <ProvidersTab metrics={data.todayProviders} inventoryPermissions={permissions} />
        : <ConfigurationTab />}
    </section>
  </>;
}

export function aiWorkerIsPaused(tenant: Pick<AiOpsConfiguration["tenant"], "ai_enabled">): boolean {
  // The pause/resume controls persist TenantProcessingPolicy.ai_analysis_enabled.
  // `processing_paused` belongs to the general Creative processing pipeline and
  // must not drive this AI-only control after a page reload.
  return !tenant.ai_enabled;
}

function MediaTypeTabs({ media, onMedia, label }: {
  media: "image" | "video"; onMedia: (media: "image" | "video") => void; label: string;
}) {
  return <div className="ops-media-tabs" role="tablist" aria-label={label}>
    {(["image", "video"] as const).map(kind => <button key={kind} type="button" role="tab" aria-selected={media === kind} className={media === kind ? "active" : ""} onClick={() => onMedia(kind)}>{kind === "image" ? "Image AI" : "Video AI"}</button>)}
  </div>;
}

function AiWorkerToggle({ workers }: { workers: NonNullable<AiOpsDashboardData["media"]>["workers"] }) {
  const [imagePaused, setImagePaused] = useState<boolean | null>(null);
  const [videoPaused, setVideoPaused] = useState<boolean | null>(null);
  const [allowed, setAllowed] = useState(false);
  const [pending, setPending] = useState<"image" | "video" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    fetchAiOperationsConfiguration().then(configuration => {
      if (!alive) return;
      setImagePaused(aiWorkerIsPaused(configuration.tenant));
      setVideoPaused(configuration.tenant.video_enabled === false);
      setAllowed(Boolean(configuration.permissions.can_emergency_stop));
    }).catch(() => {
      if (!alive) return;
      setImagePaused(null);
      setVideoPaused(null);
    });
    return () => { alive = false; };
  }, []);

  async function toggle(kind: "image" | "video") {
    const paused = kind === "image" ? imagePaused : videoPaused;
    if (paused === null || !allowed || pending) return;
    const nextPaused = !paused;
    setPending(kind);
    setError("");
    try {
      if (kind === "image") {
        await setTenantAiPaused(nextPaused, nextPaused
          ? "AI image processing paused from Operations dashboard"
          : "AI image processing resumed from Operations dashboard");
        setImagePaused(nextPaused);
      } else {
        await setVideoAiPaused(nextPaused, nextPaused
          ? "AI video processing paused from Operations dashboard"
          : "AI video processing resumed from Operations dashboard");
        setVideoPaused(nextPaused);
      }
    } catch (reason) {
      setError(String((reason as Error)?.message || "Could not update AI processing"));
    } finally {
      setPending(null);
    }
  }

  const imageEnabled = imagePaused === false;
  const videoEnabled = videoPaused === false;
  const video = workers.find(worker => worker.role === "video");
  const control = (kind: "image" | "video", enabled: boolean, paused: boolean | null) => (
    <div className="ops-worker-control">
      <span>{kind === "image" ? "AI Image" : "AI Video"}</span>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={"Toggle AI " + (kind === "image" ? "Image" : "Video") + " processing"}
        title={kind === "video" && enabled && video?.ready === false ? "Video worker is not ready" : undefined}
        disabled={!allowed || paused === null || pending !== null}
        onClick={() => void toggle(kind)}
        className={enabled ? "on" : "off"}
      >
        <i aria-hidden="true" /><b>{pending === kind ? "Updating..." : enabled ? "Enabled" : "Paused"}</b>
      </button>
    </div>
  );
  return <div className="ops-worker-controls">
    {control("image", imageEnabled, imagePaused)}
    {control("video", videoEnabled, videoPaused)}
    {error && <small className="ops-worker-error" role="alert">{error}</small>}
  </div>;
}

export function handleTabKeyDown(event: React.KeyboardEvent<HTMLElement>, active: AiOpsTab, onTab: (tab: AiOpsTab) => void) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const current = tabs.findIndex(item => item.id === active);
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : event.key === "ArrowRight" ? (current + 1) % tabs.length : (current - 1 + tabs.length) % tabs.length;
  onTab(tabs[next].id);
  event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
}

export function AiOperationsFilters({ filters, models, profiles, onChange }: {
  filters: AiOpsFilters; models: string[]; profiles: string[];
  onChange: (value: AiOpsFilters) => void;
}) {
  const field = (changes: Partial<AiOpsFilters>) => onChange({ ...filters, ...changes, page: 1, usagePage: 1, videoPage: 1 });
  return <form className="ops-filters" aria-label="Dashboard filters" onSubmit={event => event.preventDefault()}>
    <label>Date range<select aria-label="Date range" value={filters.range} onChange={event => field({ range: Number(event.target.value) as 0 | 30 | 90 | 180 })}><option value="30">Last 1 month</option><option value="90">Last 3 months</option><option value="180">Last 6 months</option><option value="0">All time</option></select></label>
    <label>Provider<select aria-label="Provider" value={filters.provider} onChange={event => field({ provider: event.target.value })}><option value="">All providers</option><option value="gemini">Google Gemini</option><option value="openai">OpenAI</option></select></label>
    <label>Model<input aria-label="Model" list="ops-models" value={filters.model} onChange={event => field({ model: event.target.value })} placeholder="All models" /><datalist id="ops-models">{models.map(model => <option key={model} value={model} />)}</datalist></label>
    <label>Mode<select aria-label="Processing mode" value={filters.processingMode} onChange={event => field({ processingMode: event.target.value })}><option value="">All modes</option><option value="single">Single</option><option value="batch">Batch</option></select></label>
    <label>Metadata profile<input aria-label="Metadata profile" list="ops-profiles" value={filters.metadataProfile} onChange={event => field({ metadataProfile: event.target.value })} placeholder="All profiles" /><datalist id="ops-profiles">{profiles.map(profile => <option key={profile} value={profile} />)}</datalist></label>
    <label>Status<select aria-label="Processing status" value={filters.status} onChange={event => field({ status: event.target.value })}><option value="">All statuses</option><option value="waiting">Đang chờ</option><option value="queued">Đã xếp hàng</option><option value="running">Đang chạy</option><option value="completed">Hoàn tất</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></select></label>
  </form>;
}

function scanStatusLabel(status: string): string {
  return ({
    completed: "Hoàn thành",
    running: "Đang quét",
    processing: "Đang quét",
    queued: "Chờ quét",
    waiting: "Chờ quét",
    failed: "Cần kiểm tra",
    cancelled: "Đã hủy",
  } as Record<string, string>)[status] || status.replaceAll("_", " ");
}

function PipelineContextIcon({ kind }: { kind: "sync" | "active" | "ready" }) {
  const paths = kind === "sync"
    ? <>
      <path d="M5.2 14.3a4.1 4.1 0 1 1 1-7.7A4.6 4.6 0 0 1 15 8.8a3 3 0 0 1-.3 5.9H5.2Z" />
      <path d="M6.2 10.4a4.4 4.4 0 0 1 5.1-1.8M12.9 8.6v2.4h-2.4M13.8 12.2a4.4 4.4 0 0 1-5.1 1.8M7.1 14v-2.4h2.4" />
    </>
    : kind === "active"
      ? <>
        <path d="M5.2 14.3a4.1 4.1 0 1 1 1-7.7A4.6 4.6 0 0 1 15 8.8a3 3 0 0 1-.3 5.9H5.2Z" />
        <path d="M10 8.8v5.1M7.9 11.8 10 14l2.1-2.2" />
      </>
      : <>
        <path d="M5 3.5h5.8l3.2 3.2v7.8A1.5 1.5 0 0 1 12.5 16h-7A1.5 1.5 0 0 1 4 14.5V5A1.5 1.5 0 0 1 5 3.5Z" />
        <path d="M10.5 3.8v3h3M6.5 11.2l1.7 1.7 3.4-3.5" />
      </>;
  return <span className={"pipeline-context-icon " + kind} aria-hidden="true">
    <svg viewBox="0 0 20 20" fill="none">{paths}</svg>
  </span>;
}

function ScanStatusIcon({ status }: { status: string }) {
  const state = ["running", "processing"].includes(status)
    ? "running"
    : ["queued", "waiting"].includes(status)
      ? "waiting"
      : status === "completed"
        ? "completed"
        : ["failed", "cancelled"].includes(status)
          ? "failed"
          : "idle";
  return <span className={"scan-status-icon " + state} aria-hidden="true">
    {state === "completed" ? "✓" : state === "failed" ? "!" : <i />}
  </span>;
}

export function PipelineOverview({ pipeline, mediaDashboard = null, media = "image", onMedia = () => undefined, onOpenAsset = () => undefined, onOpenVideo = () => undefined, onPage = () => undefined, onVideoPage = () => undefined }: {
  pipeline?: PipelineSnapshot | null; mediaDashboard?: AiOpsDashboardData["media"]; media?: "image" | "video"; onMedia?: (media: "image" | "video") => void;
  onOpenAsset?: (assetId: string) => void;
  onOpenVideo?: (sourceAssetId: string) => void;
  onPage?: (page: number, pageSize: 25 | 50 | 100) => void; onVideoPage?: (page: number, pageSize: 25 | 50 | 100) => void;
}) {
  if (pipeline === undefined) return <DashboardState kind="empty" label="Tổng quan pipeline chưa khả dụng cho đến khi API được cập nhật và khởi động lại." />;
  if (pipeline === null) return <DashboardState kind="empty" label="Chưa có hoạt động pipeline cho workspace này" />;
  if (media === "video" && mediaDashboard) return <VideoPipelineOverview dashboard={mediaDashboard} onPage={onVideoPage} onOpenVideo={onOpenVideo} />;
  const scan = pipeline.latest_source_sync;
  const active = pipeline.active_job;
  const needsAttentionStages = pipeline.stages.filter(stage => stage.needs_attention_assets > 0 || stage.processing_assets > 0 || stage.queued_assets > 0 || stage.waiting_assets > 0);
  const settledStages = pipeline.stages.filter(stage => !needsAttentionStages.includes(stage) && stage.completed_assets > 0);
  return <div className="ops-content pipeline-content">
    <section className="pipeline-summary" aria-label="Tóm tắt pipeline">
      <PipelineMetric icon="eligible" label="Ảnh đủ điều kiện" value={pipeline.overall.eligible_assets ?? pipeline.overall.supported_assets} detail="Bản ghi ảnh duy nhất từ các nguồn đang hoạt động" />
      <PipelineMetric icon="ready" label="Sẵn sàng tìm kiếm" value={pipeline.overall.search_ready_assets ?? pipeline.overall.completed} detail={pipeline.overall.indexed_percentage === null ? "Đang tính tiến độ" : String(pipeline.overall.indexed_percentage) + "% ảnh đủ điều kiện"} tone="success" />
      <PipelineMetric icon="active" label="Đang xử lý" value={pipeline.overall.in_progress_assets ?? pipeline.overall.active} detail="Tài sản duy nhất đang được xử lý" tone="info" />
      <PipelineMetric icon="queued" label="Đang chờ xử lý" value={pipeline.overall.queued_assets ?? pipeline.overall.queued} detail="Tài sản duy nhất đang chờ bắt đầu hoặc thử lại" tone="warning" />
      <PipelineMetric icon="attention" label="Cần xử lý" value={pipeline.overall.needs_attention_assets ?? pipeline.overall.failed} detail="Tài sản chưa hoàn tất cần can thiệp" tone="attention" />
    </section>
    <div className="pipeline-context-row" role="group" aria-label="Latest scan and current processing">
      <section className="pipeline-scan-card pipeline-scan-card-compact" aria-label="Trạng thái quét Google Drive">
        <div className="pipeline-context-heading"><small>ĐỒNG BỘ GOOGLE DRIVE</small><div className="pipeline-context-title"><PipelineContextIcon kind="sync" /><h2>{scan ? (scan.mode === "full" ? "Lần quét toàn bộ gần nhất" : "Lần quét cập nhật gần nhất") : "Chưa có lần quét nào"}</h2></div>
        <p>{scan ? (scan.status === "completed" ? scan.items_seen_count.toLocaleString() + " mục trên Google Drive đã được ghi nhận. " + scan.jobs_created_count.toLocaleString() + " tác vụ xử lý đã được xếp hàng." : "Hệ thống đang quét. Đã ghi nhận " + scan.items_seen_count.toLocaleString() + " mục cho đến thời điểm này.") : "Kết nối Google Drive để hệ thống phát hiện tài sản và chuẩn bị xử lý."}</p></div>
        {scan && <dl><div><dt>Trạng thái</dt><dd><span className="scan-status"><ScanStatusIcon status={scan.status} />{scanStatusLabel(scan.status)}</span></dd></div><div><dt>{scan.completed_at ? "Hoàn tất lúc" : "Thời gian"}</dt><dd>{scan.completed_at ? new Date(scan.completed_at).toLocaleString() : "Đang thực hiện"}</dd></div></dl>}
      </section>
      {active && <section className="pipeline-active-job" aria-live="polite"><div className="pipeline-context-heading"><small>ĐANG XỬ LÝ</small><div className="pipeline-context-title"><PipelineContextIcon kind="active" /><div className="pipeline-active-title"><span className="pipeline-status-dot active" aria-hidden="true" /><h2>{pipelineStageLabel(active.stage)}</h2></div></div><p>{active.message}</p></div><dl>{active.filename && <div><dt>Tài sản</dt><dd>{active.filename}</dd></div>}<div><dt>Bắt đầu</dt><dd>{active.started_at ? new Date(active.started_at).toLocaleString() : "-"}</dd></div><div><dt>Thời gian đã chạy</dt><dd>{formatProcessingDuration(active.elapsed_ms)}</dd></div><div><dt>Lần thử</dt><dd>{active.attempt_count}/{active.max_attempts}</dd></div></dl></section>}
    <section className="pipeline-progress-summary pipeline-progress-summary-compact" aria-label="Mức sẵn sàng của tài sản theo giai đoạn đã xác thực"><div className="pipeline-context-heading"><small>MỨC SẴN SÀNG CỦA TÀI SẢN</small><div className="pipeline-context-title"><PipelineContextIcon kind="ready" /><h2>Giai đoạn hoàn tất đã xác thực</h2></div><p>Mỗi ảnh đủ điều kiện chỉ được tính một lần.</p></div><dl>{pipeline.overall.asset_progress.filter(item => item.count > 0).map(item => <div key={item.key}><dt>{assetProgressLabel(item.key)}</dt><dd>{item.count.toLocaleString()}</dd></div>)}</dl></section>
    </div>
    {needsAttentionStages.length > 0 && <section className="pipeline-stage-section" aria-label="Các giai đoạn cần xử lý"><header><div><small>ĐIỂM CẦN LƯU Ý</small><h2>Các giai đoạn đang hoạt động</h2><p>Chỉ các giai đoạn đang chờ, đang chạy hoặc gặp lỗi mới được mở rộng.</p></div><span>{needsAttentionStages.length} giai đoạn</span></header><div className="pipeline-stage-grid">{needsAttentionStages.map(stage => { const tone = pipelineStageTone(stage, active?.job_type); return <article key={stage.key} className={tone}>
      <header><div><small>GIAI ĐOẠN</small><h2>{pipelineStageLabel(stage.label)}</h2></div><StageStatusBadge tone={tone} /></header><p>{stage.subtitle}</p>
      <strong>{stage.completed_assets.toLocaleString()} / {stage.total_logical_assets ? stage.total_logical_assets.toLocaleString() : "-"}</strong><span>{stage.percentage === null ? "Đang tính tiến độ" : String(stage.percentage) + "% tài sản logic đã hoàn tất"}</span><div className={"pipeline-progress " + tone}><i style={{ width: String(stage.percentage || 0) + "%" }} /></div>
      <dl><div><dt>Đã xếp hàng</dt><dd>{(stage.queued_assets ?? stage.pending).toLocaleString()}</dd></div><div><dt>Đang chạy</dt><dd>{(stage.processing_assets ?? stage.processing).toLocaleString()}</dd></div><div><dt>Đang chờ</dt><dd>{(stage.waiting_assets ?? stage.waiting).toLocaleString()}</dd></div><div><dt>Cần xử lý</dt><dd>{(stage.needs_attention_assets ?? stage.failed).toLocaleString()}</dd></div></dl>
    </article>; })}</div></section>}
    {settledStages.length > 0 && <section className="pipeline-settled-stages" aria-label="Các giai đoạn đã hoàn tất"><span><i aria-hidden="true">✓</i> Giai đoạn đã hoàn tất</span><ul>{settledStages.map(stage => <li key={stage.key}><b>{pipelineStageLabel(stage.label)}</b><span>{stage.completed_assets.toLocaleString()} tài sản đã hoàn tất</span></li>)}</ul></section>}
    <section className="pipeline-queue" aria-label="Hàng đợi pipeline hiện tại"><header><div><small>HÀNG ĐỢI TRỰC TIẾP</small><h2>Phân bổ hàng đợi theo giai đoạn</h2><p>Chỉ hiển thị các giai đoạn đang xử lý, chờ hoặc cần can thiệp. Số liệu là tài sản duy nhất, không phải số lần thử.</p></div><span className="pipeline-queue-total">{(pipeline.overall.queued_assets ?? pipeline.overall.queued).toLocaleString()} đang chờ xử lý</span></header>{needsAttentionStages.length ? <div className="ops-table-scroll"><table className="ops-data-table pipeline-queue-table"><caption className="sr-only">Công việc hiện tại theo giai đoạn pipeline</caption><thead><tr className="pipeline-queue-groups"><th rowSpan={2}>Giai đoạn</th><th colSpan={3}>Chờ bắt đầu</th><th rowSpan={2}>Đang xử lý</th><th colSpan={2}>Kết quả</th></tr><tr><th title="Assets with a queued stage, including work waiting on a prerequisite">Đã xếp hàng</th><th title="Đã xếp hàng assets that a worker can claim now">Sẵn sàng</th><th title="Assets delayed until a scheduled retry or provider quota window">Đã lên lịch thử lại</th><th title="Assets currently held by a worker">Đang chạy</th><th>Hoàn tất</th><th>Cần xử lý</th></tr></thead><tbody>{needsAttentionStages.map(stage => <tr key={stage.key}><td><b>{pipelineStageLabel(stage.label)}</b><small>{stage.subtitle}</small></td><td><QueueCount value={stage.queued_assets} tone="neutral" /></td><td><QueueCount value={stage.eligible_now_assets} tone="ready" /></td><td><QueueCount value={stage.waiting_assets} tone="waiting" /></td><td><QueueCount value={stage.processing_assets} tone="active" /></td><td><QueueCount value={stage.completed_assets} tone="complete" /></td><td><QueueCount value={stage.needs_attention_assets} tone="failed" /></td></tr>)}</tbody></table></div> : <p className="pipeline-queue-empty">{settledStages.length > 0 ? "Hiện không có giai đoạn pipeline nào cần xử lý. Các giai đoạn chỉ hoàn tất được hiển thị ở trên." : "Hiện chưa có dữ liệu hàng đợi hoặc giai đoạn hoàn tất trong phạm vi đang xem."}</p>}</section>
    <details className="pipeline-attempt-diagnostics"><summary><span className="pipeline-diagnostics-title"><i aria-hidden="true">⌁</i><span><b>Chẩn đoán lần thử</b><small>Lịch sử kỹ thuật, không dùng để tính tiến độ tài sản</small></span></span><span className="pipeline-diagnostics-total">{pipeline.stages.reduce((total, stage) => total + stage.total_attempts, 0).toLocaleString()} lần thử</span></summary><div className="pipeline-diagnostics-content"><p>{pipeline.definitions?.attempt_diagnostics || "Số lần thử của job không dùng để tính tiến độ tài sản."}</p><ul>{pipeline.stages.map(stage => <li key={stage.key}><header><span>{pipelineStageLabel(stage.label)}</span><strong>{stage.total_attempts.toLocaleString()}<small>lần thử</small></strong></header><dl><div><dt>Hoàn tất</dt><dd>{stage.completed_attempts.toLocaleString()}</dd></div><div className={stage.failed_attempts ? "has-failures" : ""}><dt>Thất bại</dt><dd>{stage.failed_attempts.toLocaleString()}</dd></div></dl></li>)}</ul></div></details>
    {pipeline.skipped_breakdown?.length ? <details className="pipeline-exclusions"><summary><span><i aria-hidden="true">–</i><b>Dữ liệu bị loại trừ</b><small>Loại trừ vĩnh viễn; không cần thử lại</small></span><strong>{pipeline.skipped_breakdown.reduce((total, item) => total + item.count, 0).toLocaleString()}</strong></summary><p>Các bản ghi này nằm ngoài pipeline ảnh và không được tính là lỗi.</p><ul>{pipeline.skipped_breakdown.map(item => <li key={item.category}><b>{item.category === "unsupported" ? "Định dạng không hỗ trợ" : item.category.replaceAll("_", " ")}</b><span>{item.count.toLocaleString()} mục</span></li>)}</ul></details> : null}
    {pipeline.failure_groups.length > 0 && <section className="pipeline-attention"><header><div><small>CẦN XỬ LÝ</small><h2>Các vấn đề cần xử lý</h2><p>Chỉ hiển thị các vấn đề chưa được giải quyết. Mã kỹ thuật vẫn được giữ để hỗ trợ.</p></div><span>{pipeline.failure_groups.length} nhóm vấn đề</span></header><ul>{pipeline.failure_groups.map(item => <PipelineFailureCard key={item.stage + item.error_code} item={item} />)}</ul></section>}
    <PipelineRecentAssets recent={pipeline.recent_assets} onPage={onPage} onOpenAsset={onOpenAsset} />
  </div>;
}
function VideoThumbnailWithDuration({ thumbnailUrl, durationMs }: { thumbnailUrl: string | null; durationMs: number | null | undefined }) {
  const duration = formatVideoDuration(durationMs);
  return <span className="video-thumbnail-with-duration">
    {thumbnailUrl ? <img src={thumbnailUrl} alt="" loading="lazy" /> : <span className="video-recent-placeholder" aria-hidden="true">&#9654;</span>}
    {duration !== "—" ? <span className="video-duration-badge" aria-label={"Thời lượng " + duration}><span aria-hidden="true">♪</span>{duration}</span> : null}
  </span>;
}

function VideoPipelineOverview({ dashboard, onPage, onOpenVideo }: {
  dashboard: NonNullable<AiOpsDashboardData["media"]>; onPage: (page: number, pageSize: 25 | 50 | 100) => void;
  onOpenVideo: (sourceAssetId: string) => void;
}) {
  const analysis = dashboard.video;
  const indexing = dashboard.video_indexing;
  const stages = [analysis, indexing];
  const recent = dashboard.recent_video;
  return <div className="ops-content pipeline-content">
    <section className="pipeline-summary" aria-label="Tóm tắt video pipeline">
      <PipelineMetric icon="eligible" label="Video analysis" value={analysis.completed} detail="Video AI analyses completed" />
      <PipelineMetric icon="ready" label="Video indexed" value={indexing.completed} detail="Ready for video search" tone="success" />
      <PipelineMetric icon="active" label={"Đang xử lý"} value={analysis.running + indexing.running} detail="Video jobs currently processing" tone="info" />
      <PipelineMetric icon="queued" label={"Đang chờ xử lý"} value={analysis.queued + indexing.queued} detail="Video jobs waiting to start or retry" tone="warning" />
      <PipelineMetric icon="attention" label={"Cần xử lý"} value={analysis.failed + indexing.failed} detail="Video jobs that need review" tone="attention" />
    </section>
    <section className="pipeline-stage-section" aria-label="Video pipeline stages"><header><div><small>VIDEO PIPELINE</small><h2>Phân tích và lập chỉ mục video</h2><p>Video analysis và video indexing là hai giai đoạn độc lập.</p></div></header><div className="pipeline-stage-grid">{stages.map(stage => <article key={stage.key} className={stage.failed ? "attention" : stage.running ? "active" : stage.queued ? "waiting" : "complete"}><header><div><small>GIAI ĐOẠN</small><h2>{stage.label}</h2></div></header><strong>{stage.completed.toLocaleString()}</strong><span>đã hoàn tất</span><dl><div><dt>Đã xếp hàng</dt><dd>{stage.queued.toLocaleString()}</dd></div><div><dt>Đang chạy</dt><dd>{stage.running.toLocaleString()}</dd></div><div><dt>Cần xử lý</dt><dd>{stage.failed.toLocaleString()}</dd></div></dl></article>)}</div></section>
    <section className="pipeline-queue" aria-label="Hàng đợi video hiện tại"><header><div><small>HÀNG ĐỢI TRỰC TIẾP</small><h2>Phân bổ hàng đợi video</h2><p>Chỉ các job video được tính ở đây; không dùng dữ liệu Image.</p></div><span className="pipeline-queue-total">{(analysis.queued + indexing.queued).toLocaleString()} đang chờ xử lý</span></header></section>
    <RecentVideoProgress recent={recent} onPage={onPage} onOpenVideo={onOpenVideo} />
  </div>;
}

const recentVideoSteps = [
  { key: "video_analyze", label: "Video analysis" },
  { key: "video_search_index", label: "Video indexing" },
] as const;

type RecentVideoItem = NonNullable<AiOpsDashboardData["media"]>["recent_video"]["items"][number];

function RecentVideoStepStatus({ item, stepKey }: {
  item: RecentVideoItem; stepKey: typeof recentVideoSteps[number]["key"];
}) {
  const step = item.steps?.find(candidate => candidate.key === stepKey);
  const status = step?.status || (stepKey === "video_analyze" ? item.status : "not_started");
  const attemptCount = step?.attempt_count ?? (stepKey === "video_analyze" ? item.attempt_count : 0);
  const maxAttempts = step?.max_attempts ?? (stepKey === "video_analyze" ? item.max_attempts : 0);
  return <td data-video-step={stepKey}><div className="video-step-status">
    <StatusText status={status} />
    {maxAttempts > 0 ? <small>{attemptCount}/{maxAttempts} lần thử</small> : null}
  </div></td>;
}

function RecentVideoProgress({ recent, onPage, onOpenVideo }: {
  recent: NonNullable<AiOpsDashboardData["media"]>["recent_video"];
  onPage: (page: number, pageSize: 25 | 50 | 100) => void;
  onOpenVideo: (sourceAssetId: string) => void;
}) {
  const pages = Math.max(1, Math.ceil(recent.total / recent.page_size));
  const page = Math.min(recent.page, pages);
  const first = recent.total ? (page - 1) * recent.page_size + 1 : 0;
  const last = Math.min(page * recent.page_size, recent.total);
  return <section className="pipeline-recent" aria-label="Tiến độ video gần đây">
    <div className="ops-table-heading">
      <div><h2>Tiến độ video gần đây</h2><p>{recent.total
        ? "Hiển thị " + first + "-" + last + " trên tổng số " + recent.total + " video. Mỗi cột giai đoạn hiển thị trạng thái xử lý riêng của video."
        : "Chưa có video analysis job nào."}</p></div>
      {recent.items.length ? <div className="ops-pagination video-recent-pagination" aria-label="Video pagination">
        <label>Số mục mỗi trang<select aria-label="Số video mỗi trang" value={recent.page_size} onChange={event => onPage(1, Number(event.target.value) as 25 | 50 | 100)}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <nav aria-label="Video page numbers">
          <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1, recent.page_size as 25 | 50 | 100)}>Trước</button>
          {visiblePages(page, pages).map((entry, index) => entry === "ellipsis"
            ? <span className="ops-page-ellipsis" key={"video-ellipsis-" + index}>...</span>
            : <button type="button" key={entry} className={entry === page ? "active" : ""} aria-current={entry === page ? "page" : undefined} onClick={() => onPage(entry, recent.page_size as 25 | 50 | 100)}>{entry}</button>)}
          <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1, recent.page_size as 25 | 50 | 100)}>Tiếp</button>
        </nav>
      </div> : null}
    </div>
    {recent.items.length ? <div className="ops-table-scroll"><table className="ops-data-table">
      <thead><tr><th>Video</th><th>Vị trí</th>{recentVideoSteps.map(step => <th key={step.key}>{step.label}</th>)}<th>Cập nhật</th><th>Cần xử lý</th></tr></thead>
      <tbody>{recent.items.map(item => {
        const title = item.filename || "Video " + item.source_asset_id.slice(0, 8);
        const stepErrors = Array.from(new Set((item.steps || []).map(step => step.error_code).filter(Boolean)));
        return <tr key={item.job_id}>
          <td className="video-recent-title"><VideoThumbnailWithDuration thumbnailUrl={item.thumbnail_url} durationMs={item.duration_ms} /><span className="video-recent-copy"><button type="button" className="video-recent-title-button" onClick={() => onOpenVideo(item.source_asset_id)} aria-label={"Mở chi tiết " + title}>{title}</button><small className="asset-mime-type">{item.mime_type || "\u2014"}</small></span></td>
          <td className="video-recent-location" title={item.location || undefined}>{item.location || "—"}</td>
          {recentVideoSteps.map(step => <RecentVideoStepStatus key={step.key} item={item} stepKey={step.key} />)}
          <td>{new Date(item.updated_at).toLocaleString()}</td>
          <td>{stepErrors.length ? stepErrors.join(", ") : item.error_code || "-"}</td>
        </tr>;
      })}</tbody>
    </table></div> : <p>Chưa có video analysis job nào.</p>}
  </section>;
}

type PipelineStageState = "attention" | "active" | "waiting" | "complete" | "idle";

function pipelineStageTone(stage: { key: string; queued_assets: number; waiting_assets?: number; processing_assets: number; completed_assets: number; needs_attention_assets: number }, activeJobType?: string): PipelineStageState {
  if (stage.needs_attention_assets > 0) return "attention";
  if (stage.key === activeJobType || stage.processing_assets > 0) return "active";
  if ((stage.waiting_assets || 0) > 0 || stage.queued_assets > 0) return "waiting";
  if (stage.completed_assets > 0) return "complete";
  return "idle";
}



function StageStatusBadge({ tone }: { tone: PipelineStageState }) {
  const labels: Record<PipelineStageState, string> = { attention: "Cần xử lý", active: "Đang xử lý", waiting: "Đang chờ", complete: "Hoàn tất", idle: "Chưa bắt đầu" };
  const icons: Record<PipelineStageState, string> = { attention: "!", active: "↻", waiting: "◷", complete: "✓", idle: "–" };
  return <span className={"pipeline-stage-status " + tone}><i aria-hidden="true">{icons[tone]}</i>{labels[tone]}</span>;
}

function QueueCount({ value, tone }: { value: number; tone: "neutral" | "ready" | "waiting" | "active" | "complete" | "failed" }) {
  const count = Math.max(0, value || 0);
  return <span className={"pipeline-queue-count " + tone + (count === 0 ? " is-zero" : "")} aria-label={count.toLocaleString() + " tài sản"}>{count === 0 ? "—" : count.toLocaleString()}</span>;
}

const pipelineFailureGuidance: Record<string, { title: string; guidance: string }> = {
  InvalidPipelineContent: { title: "Invalid source record", guidance: "The source item is incomplete or invalid. Review the source record before retrying." },
  gemini_http_error: { title: "Gemini request could not complete", guidance: "Temporary provider errors may retry automatically. Check the provider status only if this keeps recurring." },
  analysis_image_dimensions: { title: "Image dimensions are not supported", guidance: "The image could not be prepared safely for analysis. Use a valid supported image before retrying." },
  unsupported_source_mime_type: { title: "Unsupported file type", guidance: "JPEG, PNG, WebP, AVIF, HEIC, and HEIF images enter the image-analysis pipeline. Modern image formats are converted safely to RGB JPEG before AI analysis." },
  source_content_too_large: { title: "Image is too large", guidance: "Reduce the source image dimensions or file size, then retry the pipeline stage." },
  search_index_unconfigured: { title: "Search indexing is not configured", guidance: "Configure Elasticsearch and the active search index before retrying indexing." },
};

function PipelineFailureCard({ item }: { item: PipelineSnapshot["failure_groups"][number] }) {
  const details = pipelineFailureGuidance[item.error_code] || { title: "Pipeline job needs review", guidance: "Review this error code and the source item before deciding whether to retry." };
  return <li className="pipeline-attention-card"><span className="pipeline-attention-icon" aria-hidden="true">!</span><div><header><span className="pipeline-stage-chip">{item.stage}</span><b>{details.title}</b></header><p>{details.guidance}</p><footer><code>{item.error_code}</code><span>{item.count.toLocaleString()} affected</span><time dateTime={item.latest_at}>Last seen {new Date(item.latest_at).toLocaleString()}</time></footer></div></li>;
}

function pipelineAssetKind(filename: string): "image" | "video" | "document" | "asset" {
  const extension = filename.split(".").at(-1)?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "webp", "avif", "heic", "heif", "gif", "svg"].includes(extension)) return "image";
  if (["mp4", "mov", "avi", "webm", "mkv"].includes(extension)) return "video";
  if (["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"].includes(extension)) return "document";
  return "asset";
}

function pipelineAssetTitle(filename: string): string {
  const looksLikeId = /^[a-f0-9]{24,}$/i.test(filename);
  return looksLikeId ? "Asset " + filename.slice(0, 8) + "..." + filename.slice(-5) : filename;
}

function PipelineAssetIcon({ filename }: { filename: string }) {
  const kind = pipelineAssetKind(filename);
  const paths = {
    image: "M4 5h16v14H4z M6.5 16l3.5-4 2.5 3 2-2 3.5 3",
    video: "M4 6h11v12H4z M15 10l5-3v10l-5-3",
    document: "M7 3h7l4 4v14H7z M14 3v5h5 M10 12h5M10 16h5",
    asset: "M7 3h7l4 4v14H7z M14 3v5h5 M10 12h5M10 16h4",
  };
  return <span className={"pipeline-asset-icon " + kind} aria-hidden="true"><svg viewBox="0 0 24 24"><path d={paths[kind]} /></svg></span>;
}

function PipelineAssetThumbnail({ filename, thumbnailUrl }: { filename: string; thumbnailUrl?: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!thumbnailUrl || failed) return <PipelineAssetIcon filename={filename} />;
  return <span className="pipeline-asset-thumbnail"><img src={thumbnailUrl} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} /></span>;
}

function pipelineStageLabel(label: string): string {
  const normalized = label.replaceAll("_", " ").toLowerCase();
  return ({
    download: "Tải xuống",
    store: "Lưu trữ",
    "ai analyze": "Phân tích AI",
    "search projection": "Chuẩn bị dữ liệu tìm kiếm",
    "elasticsearch index": "Lập chỉ mục tìm kiếm",
  } as Record<string, string>)[normalized] || label;
}

function PipelineCurrentState({ state }: { state: string }) {
  const labels: Record<string, string> = {
    discovered: "Discovered",
    stored: "Đã lưu trữ",
    analyzing: "Analyzing",
    metadata_ready: "Metadata ready",
    search_pending: "Search pending",
    indexing: "Indexing",
    indexed: "Sẵn sàng tìm kiếm",
    search_failed: "Search failed",
    duplicate: "Duplicate",
    failed: "Failed",
  };
  const tone = ["analyzing", "indexing"].includes(state)
    ? "active"
    : ["search_pending", "discovered"].includes(state)
      ? "waiting"
      : ["failed", "search_failed"].includes(state)
        ? "failed"
        : state === "duplicate"
          ? "duplicate"
          : ["stored", "metadata_ready", "indexed"].includes(state)
            ? "complete"
            : "neutral";
  const icon = tone === "active" ? ">" : tone === "complete" ? "+" : tone === "failed" ? "!" : tone === "duplicate" ? "=" : "-";
  const label = labels[state] || state.replaceAll("_", " ");
  return <span className={"pipeline-current-state " + tone} aria-label={"Trạng thái hiện tại: " + label}><i aria-hidden="true">{icon}</i><span>{label}</span></span>;
}

function PipelineRecentAssets({ recent, onPage, onOpenAsset }: { recent: PipelineSnapshot["recent_assets"]; onPage: (page: number, pageSize: 25 | 50 | 100) => void; onOpenAsset: (assetId: string) => void }) {
  if (!recent.items.length) return <section className="pipeline-recent"><h2>Tiến độ tài sản gần đây</h2><p>Chưa có tài sản pipeline nào được tạo.</p></section>;
  const pages = Math.max(1, Math.ceil(recent.total / recent.page_size));
  const page = Math.min(recent.page, pages);
  const first = (page - 1) * recent.page_size + 1;
  const last = Math.min(page * recent.page_size, recent.total);
  return <section className="pipeline-recent"><div className="ops-table-heading"><div><h2>Tiến độ tài sản gần đây</h2><p>Hiển thị {first}-{last} trên tổng số {recent.total} tài sản logic. Chọn tên để xem chi tiết ngay trong AI Operations.</p></div><div className="ops-pagination" aria-label="Pipeline asset pagination"><label>Số mục mỗi trang<select aria-label="Số mục pipeline mỗi trang" value={recent.page_size} onChange={event => onPage(1, Number(event.target.value) as 25 | 50 | 100)}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label><nav aria-label="Pipeline asset page numbers"><button type="button" disabled={page <= 1} onClick={() => onPage(page - 1, recent.page_size as 25 | 50 | 100)}>Trước</button>{visiblePages(page, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" key={"pipeline-ellipsis-" + index}>...</span> : <button type="button" key={entry} className={entry === page ? "active" : ""} aria-current={entry === page ? "page" : undefined} onClick={() => onPage(entry, recent.page_size as 25 | 50 | 100)}>{entry}</button>)}<button type="button" disabled={page >= pages} onClick={() => onPage(page + 1, recent.page_size as 25 | 50 | 100)}>Tiếp</button></nav></div></div><div className="ops-table-scroll"><table className="ops-data-table"><thead><tr><th>Tài sản</th><th>Giai đoạn hiện tại</th><th>Tải xuống</th><th>Lưu trữ</th><th>Phân tích AI</th><th>Tìm kiếm</th><th>Lập chỉ mục</th><th>Cập nhật</th><th>Cần xử lý</th></tr></thead><tbody>{recent.items.map(item => <tr key={item.asset_id || item.filename}><td className="pipeline-asset-cell"><div className="pipeline-asset"><PipelineAssetThumbnail filename={item.filename} thumbnailUrl={item.thumbnail_url} /><div>{item.asset_id ? <button type="button" className="pipeline-asset-link" onClick={() => onOpenAsset(item.asset_id!)} title={item.filename}>{pipelineAssetTitle(item.filename)}</button> : <span title={item.filename}>{pipelineAssetTitle(item.filename)}</span>}<small className="asset-mime-type">{item.mime_type || "\u2014"}</small></div></div></td><td><PipelineCurrentState state={item.state} /></td>{(["download", "store", "analyze", "projection", "index"] as const).map(stage => <td key={stage}><StatusText status={item.stage_statuses[stage] || "not_started"} /></td>)}<td>{new Date(item.updated_at).toLocaleString()}</td><td>{item.error_code || "-"}</td></tr>)}</tbody></table></div></section>;
}

function PipelineMetric({ icon, label, value, detail, tone = "" }: { icon: string; label: string; value: number; detail: string; tone?: string }) {
  return <article className={tone}><span className={"pipeline-metric-heading pipeline-icon-" + icon}><i aria-hidden="true" /><span>{label}</span></span><strong>{value.toLocaleString()}</strong><small>{detail}</small></article>;
}

function assetProgressLabel(key: string): string {
  return ({ discovered: "Đã phát hiện", downloaded: "Đã tải xuống", stored: "Đã lưu trữ", analyzed: "Đã phân tích", projection_ready: "Đã chuẩn bị tìm kiếm", search_ready: "Sẵn sàng tìm kiếm", projection_built: "Đã chuẩn bị tìm kiếm", indexed: "Sẵn sàng tìm kiếm" } as Record<string, string>)[key] || key.replaceAll("_", " ");
}

function opsKpiIcon(label: string): string {
  if (/failed|cần xử lý/i.test(label)) return "!";
  if (/success|hoàn tất/i.test(label)) return "✓";
  if (/cost|budget/i.test(label)) return "$";
  if (/rate|quota|chờ/i.test(label)) return "◷";
  if (/running|đang chạy/i.test(label)) return "↻";
  return "▣";
}

function MediaOverview({ dashboard, media }: { dashboard: NonNullable<AiOpsDashboardData["media"]>; media: "image" | "video" }) {
  const primary = media === "image" ? dashboard.image : dashboard.video;
  const cards = [
    ...(media === "video" ? [
      { label: "Processed today", value: dashboard.video_processed_today, detail: "Completed video analyses today (UTC)", tone: "neutral" },
    ] : []),
    { label: "Processed", value: primary.completed, detail: "Completed AI analyses", tone: "success" },
    { label: "Failed", value: primary.failed, detail: "Terminal AI failures", tone: "danger" },
    { label: "Running", value: primary.running, detail: "Currently processing", tone: "info" },
    ...(media === "video" ? [{ label: "Deferred by quota", value: primary.deferred_by_quota || 0, detail: primary.next_quota_retry_at ? "Next retry " + new Date(primary.next_quota_retry_at).toLocaleString() : "No quota deferral", tone: "warning" }] : []),
    { label: "Queued", value: primary.queued, detail: primary.eligible_now + " eligible now", tone: "neutral" },
    ...(media === "video" ? [
      { label: "Indexed", value: dashboard.video_indexing.completed, detail: "Video search indexing (not AI)", tone: "neutral" },
      { label: "Video cost", value: "—", detail: "Cost data unavailable", tone: "neutral" },
    ] : []),
  ];
  return <>
    <section className={`ops-kpis${media === "video" ? " ops-kpis-video" : ""}`} aria-label={media + " AI processing summary"}>{cards.map(card => <article key={card.label} className={"ops-kpi ops-kpi-" + card.tone}><span className="ops-kpi-title"><i aria-hidden="true">{opsKpiIcon(card.label)}</i>{card.label}</span><strong>{card.value.toLocaleString()}</strong><small>{card.detail}</small></article>)}</section>
  </>;
}

function Overview({ data, media = "image", onMedia = () => undefined, canManage, onRefresh }: {
  data: AiOpsDashboardData; media?: "image" | "video"; onMedia?: (media: "image" | "video") => void;
  canManage: boolean; onRefresh: () => void;
}) {
  const summary = data.summary;
  if (media === "video" && data.media) {
    return <div className="ops-content">
      <p className="ops-ai-scope-note">Video AI metrics are calculated only from video analysis jobs. Video indexing is displayed separately and is not counted as AI completion, failure, running, or cost.</p>
      <MediaOverview dashboard={data.media} media="video" />
      <section className="ops-charts" aria-label="Video AI analytics">
        <AccessibleChart title="Daily processing" description="Completed and failed video analyses by UTC day." data={dailyStatusChart(data.media.analytics.daily)} />
        <AccessibleChart
          title="Daily estimated cost by provider"
          description={data.media.analytics.cost_available
            ? "Estimated Video AI provider cost for the selected period."
            : "Video AI cost is not recorded by the current worker."}
          data={data.media.analytics.cost_available ? dailyProviderCostChart(data.media.analytics.daily) : []}
          valueLabel={value => formatCost(value)}
        />
        <AccessibleChart title="Provider and mode volume" description="Video analysis volume grouped by provider and processing mode." data={providerVolumeChart(data.media.analytics.providers)} />
        <AccessibleChart title="Failure categories" description="Stable Video AI failure codes; raw exception messages are excluded." data={failureChart(data.media.analytics.failures)} />
        <AccessibleChart
          title="Latency"
          description="Average and p95 Video AI latency for the selected period."
          data={[{ label: "Latency", values: { Average: data.media.analytics.latency.average_ms, p95: data.media.analytics.latency.p95_ms } }]}
          valueLabel={value => `${Math.round(value)} ms`}
        />
      </section>
    </div>;
  }
  if (!summary && !data.daily.length) return <DashboardState kind="empty" />;
  const processedToday = (data.today?.completed || 0) + (data.today?.failed || 0);
  const cards = [
    { label: "Processed today", value: processedToday, detail: "Hoàn tất and failed today", tone: "neutral" },
    { label: "Hoàn tất", value: summary?.completed || 0, detail: "Finished successfully", tone: "success" },
    { label: "Failed", value: summary?.failed || 0, detail: "Cần xử lý", tone: "danger" },
    { label: "Budget blocked", value: summary?.budget_blocked || 0, detail: "Stopped by budget policy", tone: "danger" },
    { label: "Rate-limit scheduling delay", value: summary?.local_rate_limited || 0, detail: "Locally scheduled model starts", tone: "warning" },
    { label: "Đang chờ for quota", value: (summary?.quota_deferred || 0) + (summary?.provider_cooldown_deferred || 0), detail: "Provider quota or cooldown", tone: "warning" },
    { label: "Đang chạy", value: summary?.running || 0, detail: "Currently processing", tone: "info" },
    { label: "Đã xếp hàng", value: summary?.queued || 0, detail: "Chờ bắt đầu", tone: "neutral" },
    { label: "Success rate", value: `${((summary?.success_rate || 0) * 100).toFixed(1)}%`, detail: "Hoàn tất out of terminal jobs", tone: "success" },
    { label: "Estimated cost today", value: formatCost(data.today?.cost?.estimated_cost_micros, data.today?.cost?.currency), detail: "Projected usage for today", tone: "neutral" },
    { label: "Estimated cost this month", value: formatCost(data.month?.cost?.estimated_cost_micros, data.month?.cost?.currency), detail: "Projected monthly usage", tone: "neutral" },
  ];
  const nextQuotaRetry = summary?.next_provider_retry_at ?? summary?.next_quota_retry_at;
  const nextLocalRetry = summary?.next_local_rate_limit_retry_at;
  const localScheduled = summary?.local_rate_limited || 0;
  const quotaScheduled = (summary?.quota_deferred || 0) + (summary?.provider_cooldown_deferred || 0);
  return <div className="ops-content">
    <p className="ops-ai-scope-note">{media === "image"
      ? "These metrics cover AI analysis only. Tải xuống, storage, projection, and indexing are shown in Pipeline Overview."
      : "Video AI temporarily uses the same operations layout as Image AI. Video indexing is shown separately in Pipeline Overview."}</p>
    <SearchCoverageCard coverage={data.coverage} canManage={canManage} onRefresh={onRefresh} />
    {localScheduled > 0 && nextLocalRetry && <section className="ops-quota-notice" role="status" aria-label="AI model scheduling retry status">
      <div><span className="ops-quota-badge">Schedule</span><div><strong>Rate-limit scheduling delay</strong><p>{localScheduled} {localScheduled === 1 ? "analysis is" : "analyses are"} waiting for the next local model-start slot. No provider request was sent.</p></div></div>
      <time dateTime={nextLocalRetry}><span>Tiếp local slot</span>{new Date(nextLocalRetry).toLocaleString()}</time>
    </section>}
    {quotaScheduled > 0 && nextQuotaRetry && <section className="ops-quota-notice" role="status" aria-label="Gemini quota retry status">
      <div><span className="ops-quota-badge">Quota</span><div><strong>Gemini quota or provider cooldown is active</strong><p>{quotaScheduled} {quotaScheduled === 1 ? "analysis" : "analyses"} will retry automatically after the provider allows another request.</p></div></div>
      <time dateTime={nextQuotaRetry}><span>Tiếp provider retry</span>{new Date(nextQuotaRetry).toLocaleString()}</time>
    </section>}
    <section className="ops-kpis" aria-label="AI processing summary">{cards.map(card => <article key={card.label} className={`ops-kpi ops-kpi-${card.tone}`}><span className="ops-kpi-title"><i aria-hidden="true">{opsKpiIcon(card.label)}</i>{card.label}</span><strong>{card.value}</strong><small>{card.detail}</small></article>)}</section>
    <section className="ops-charts">
      <AccessibleChart title="Daily processing" description="Hoàn tất and failed analyses by UTC day." data={dailyStatusChart(data.daily)} />
      <AccessibleChart title="Daily estimated cost by provider" description="Estimated provider cost aggregated by the server for the selected period." data={dailyProviderCostChart(data.daily)} valueLabel={value => formatCost(value)} />
      <AccessibleChart title="Provider and mode volume" description="Analysis volume grouped by provider and processing mode." data={providerVolumeChart(data.providers)} />
      <AccessibleChart title="Failure categories" description="Stable internal failure codes; raw exception messages are excluded." data={failureChart(data.failures)} />
      <AccessibleChart title="Latency" description="Average and p95 provider latency for the selected period." data={[{ label: "Latency", values: { Average: summary?.latency.average_ms || 0, "p95": summary?.latency.p95_ms || 0 } }]} valueLabel={value => `${Math.round(value)} ms`} />
    </section>
  </div>;
}

export function pageFilters(filters: AiOpsFilters, page: number): AiOpsFilters {
  return { ...filters, page: Math.max(1, page) };
}

export function usagePageFilters(filters: AiOpsFilters, usagePage: number): AiOpsFilters {
  return { ...filters, usagePage: Math.max(1, usagePage) };
}

export function visiblePages(currentPage: number, totalPages: number): Array<number | "ellipsis"> {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  if (currentPage <= 4) return [1, 2, 3, 4, 5, "ellipsis", totalPages];
  if (currentPage >= totalPages - 3) return [1, "ellipsis", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  return [1, "ellipsis", currentPage - 1, currentPage, currentPage + 1, "ellipsis", totalPages];
}

function ProcessingFailureGroupRetry({
  failures, permissions, onAccepted, jobType = "asset_analyze",
}: {
  failures: AiOpsDashboardData["failures"];
  permissions: string[];
  onAccepted: () => void;
  jobType?: "asset_analyze" | "video_analyze";
}) {
  const groups = useMemo(
    () => [...new Map(failures.filter(item => item.error_code).map(item => [item.error_code, item])).values()]
      .sort((a, b) => b.count - a.count || a.error_code.localeCompare(b.error_code)),
    [failures],
  );
  const [errorCode, setErrorCode] = useState(groups[0]?.error_code || "");
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!groups.some(group => group.error_code === errorCode)) setErrorCode(groups[0]?.error_code || "");
  }, [groups, errorCode]);
  if (!permissions.includes("ai_jobs.retry") || !groups.length) return null;
  const selected = groups.find(group => group.error_code === errorCode);
  async function submit() {
    if (!errorCode || !reason.trim()) return;
    setBusy(true); setMessage("");
    try {
      const result = await retryAiOperationsJobsByError(errorCode, reason.trim(), 1000, fetch, jobType);
      setMessage(result.retried + " job" + (result.retried === 1 ? "" : "s") + " queued" + (result.skipped ? "; " + result.skipped + " skipped" : "") + ".");
      setConfirming(false);
      setReason("");
      onAccepted();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Group retry failed");
    } finally { setBusy(false); }
  }
  return <section className="ops-bulk-retry" aria-label="Retry failed jobs by error group">
    <div>
      <label htmlFor={jobType + "-failed-error-group"}>Failed error group</label>
      <select id={jobType + "-failed-error-group"} value={errorCode} onChange={event => setErrorCode(event.target.value)}>
        {groups.map(group => <option key={group.error_code} value={group.error_code}>{group.error_code} ({group.count})</option>)}
      </select>
      <small>{selected ? selected.count + " matching failed jobs" : ""} - maximum 1,000 per action</small>
    </div>
    <button type="button" disabled={!errorCode} onClick={() => { setConfirming(true); setReason(""); setMessage(""); }}>Retry failed group</button>
    {confirming && <div className="ops-confirm" role="dialog" aria-modal="true" aria-label="Confirm group retry">
      <strong>Retry {selected?.error_code || "failed jobs"}?</strong>
      <p>Only terminal failed AI jobs in this error group will be requeued. This action is audited.</p>
      <label>Reason<input autoFocus value={reason} onChange={event => setReason(event.target.value)} placeholder="Explain why these jobs should be retried" /></label>
      <div><button type="button" onClick={() => setConfirming(false)}>Cancel</button><button type="button" className="danger" disabled={busy || !reason.trim()} onClick={submit}>{busy ? "Retrying..." : "Confirm retry"}</button></div>
    </div>}
    {message && <span className="ops-action-message" aria-live="polite">{message}</span>}
  </section>;
}

export function formatErrorDetail(code: string, message?: string | null): string {
  const detail = message?.trim() || "No additional error detail was recorded.";
  return `Error code: ${code}\nMessage: ${detail}`;
}

async function copyText(value: string): Promise<void> {
  try {
    if (!navigator.clipboard) throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(value);
    return;
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand?.("copy") ?? false;
    textarea.remove();
    if (!copied) throw new Error("Copy failed");
  }
}

export function ErrorDetailPopover({ code, message }: { code: string | null | undefined; message?: string | null }) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeTimer = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"idle" | "success" | "error">("idle");
  const [position, setPosition] = useState({ left: 12, top: 12, above: false });
  const detailMessage = message?.trim() || "No additional error detail was recorded.";

  function clearCloseTimer() {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }
  function updatePosition() {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const panelWidth = Math.min(380, window.innerWidth - 24);
    setPosition({
      left: Math.max(12, Math.min(rect.left, window.innerWidth - panelWidth - 12)),
      top: rect.top > 210 ? rect.top - 8 : rect.bottom + 8,
      above: rect.top > 210,
    });
  }
  function show() {
    clearCloseTimer();
    updatePosition();
    setOpen(true);
  }
  function scheduleHide() {
    clearCloseTimer();
    closeTimer.current = window.setTimeout(() => {
      setOpen(false);
      setCopied("idle");
    }, 140);
  }
  useEffect(() => {
    if (!open) return undefined;
    const reposition = () => updatePosition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open]);
  useEffect(() => () => clearCloseTimer(), []);

  if (!code) return <span aria-label="No error">—</span>;
  const popover = open && typeof document !== "undefined" ? createPortal(
    <section
      className={`ops-error-popover ${position.above ? "is-above" : "is-below"}`}
      role="dialog"
      aria-label={`Error details for ${code}`}
      style={{ left: position.left, top: position.top, transform: position.above ? "translateY(-100%)" : undefined }}
      onMouseEnter={clearCloseTimer}
      onMouseLeave={scheduleHide}
    >
      <header><strong>Error details</strong><span>{copied === "success" ? "Copied" : copied === "error" ? "Copy failed" : "Hover or focus to inspect"}</span></header>
      <dl><div><dt>Error code</dt><dd><code>{code}</code></dd></div><div><dt>Message</dt><dd>{detailMessage}</dd></div></dl>
      <button type="button" onClick={() => void copyText(formatErrorDetail(code, message)).then(() => setCopied("success")).catch(() => setCopied("error"))}>
        <span aria-hidden="true">⧉</span> {copied === "success" ? "Copied" : "Copy details"}
      </button>
    </section>,
    document.body,
  ) : null;
  return <>
    <button ref={triggerRef} type="button" className="ops-error-trigger" aria-haspopup="dialog" aria-expanded={open} onMouseEnter={show} onMouseLeave={scheduleHide} onFocus={show} onBlur={scheduleHide}>
      <code>{code}</code><span aria-hidden="true">ⓘ</span><span className="sr-only">Show error details</span>
    </button>
    {popover}
  </>;
}

function Processing({ data, filters, permissions, onFilters, onActionAccepted, onOpenAsset, onOpenVideo, media = "image", onVideoPage = () => undefined }: {
  data: AiOpsDashboardData; filters: AiOpsFilters; permissions: string[];
  onFilters: (value: AiOpsFilters) => void; onActionAccepted: () => void;
  onOpenAsset: (assetId: string) => void;
  onOpenVideo: (sourceAssetId: string) => void;
  media?: "image" | "video"; onVideoPage?: (page: number, pageSize: 25 | 50 | 100) => void;
}) {
  if (media === "video") return <VideoProcessing
    media={data.media}
    permissions={permissions}
    onAccepted={onActionAccepted}
    onPage={onVideoPage}
    onOpenVideo={onOpenVideo}
  />;
  const usageByJob = new Map(data.usage.items.filter(item => item.job_id).map(item => [item.job_id!, item]));
  if (!data.jobs.items.length) return <DashboardState kind="empty" label="No processing jobs in this period" />;
  const pages = Math.max(1, Math.ceil(data.jobs.total / data.jobs.page_size));
  const currentPage = Math.min(Math.max(1, data.jobs.page), pages);
  const firstAsset = (currentPage - 1) * data.jobs.page_size + 1;
  const lastAsset = Math.min(currentPage * data.jobs.page_size, data.jobs.total);
  return <div className="ops-content">
    <div className="ops-table-heading">
      <div><h2>AI processing jobs</h2><p>Showing {firstAsset}-{lastAsset} of {data.jobs.total}</p></div>
      <div className="ops-pagination" aria-label="Processing pagination">
        <label>Số mục mỗi trang<select aria-label="Số mục mỗi trang" value={data.jobs.page_size} onChange={event => onFilters({ ...filters, page: 1, pageSize: Number(event.target.value) as 25 | 50 | 100 })}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <nav aria-label="Processing page numbers">
          <button type="button" aria-label="Trước page" disabled={currentPage <= 1} onClick={() => onFilters(pageFilters(filters, currentPage - 1))}>Trước</button>
          {visiblePages(currentPage, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" aria-hidden="true" key={`ellipsis-${index}`}>...</span> : <button type="button" key={entry} aria-label={`Page ${entry}`} aria-current={entry === currentPage ? "page" : undefined} className={entry === currentPage ? "active" : ""} onClick={() => onFilters(pageFilters(filters, entry))}>{entry}</button>)}
          <button type="button" aria-label="Tiếp page" disabled={currentPage >= pages} onClick={() => onFilters(pageFilters(filters, currentPage + 1))}>Tiếp</button>
        </nav>
      </div>
    </div>
    <ProcessingFailureGroupRetry failures={data.failures} permissions={permissions} onAccepted={onActionAccepted} />
    <div className="ops-table-scroll"><table className="ops-data-table">
      <caption className="sr-only">AI processing jobs</caption>
      <thead><tr>{["Status", "Asset", "Provider", "Model", "Mode", "Profile", "Attempts", "Duration", "Cost", "Error", "Actions"].map(value => <th key={value}>{value}</th>)}</tr></thead>
      <tbody>{data.jobs.items.map(job => {
        const usage = usageByJob.get(job.id);
        const mode = usage?.processing_mode || (job.job_type.startsWith("ai_batch_") ? "batch" : "single");
        const assetId = job.asset_id || usage?.asset_id || (job.entity_type === "asset" ? job.entity_id : null);
        const assetTitle = job.filename || assetId || job.entity_id;
        return <tr key={job.id}>
          <td><StatusText status={job.status} isDeferred={job.is_deferred} nextAttemptAt={job.next_attempt_at} /></td>
          <td className="processing-job-asset-cell"><div className="pipeline-asset processing-job-asset">
            <PipelineAssetThumbnail filename={assetTitle} thumbnailUrl={job.thumbnail_url} />
            <div>
              {assetId ? <button type="button" className="pipeline-asset-link" onClick={() => onOpenAsset(assetId)} title={assetTitle}>{assetTitle}</button> : <span title={assetTitle}>{assetTitle}</span>}
              <small className="asset-mime-type">{job.mime_type || "\u2014"}</small>
              {assetId && job.filename ? <code title={assetId}>{assetId}</code> : null}
            </div>
          </div></td>
          <td>{providerLabel(job.provider)}</td><td>{usage?.model || "\u2014"}</td><td>{modeLabel(mode)}</td>
          <td>{usage?.metadata_profile || "\u2014"}</td><td>{job.attempt_count}/{job.max_attempts}</td>
          <td>{job.status === "processing" ? formatDuration(job.claimed_at, job.updated_at) : formatProcessingDuration(job.processing_duration_ms)}</td>
          <td>{formatCost(usage?.estimated_cost_micros, usage?.currency)}</td><td><ErrorDetailPopover code={job.error?.code} message={job.error?.message} /></td>
          <td><div className="ops-job-actions">
            {assetId ? <button type="button" aria-label={`View asset ${assetId}`} onClick={() => onOpenAsset(assetId)}>Chi tiết</button> : <span title="Asset identity is not available yet">Unavailable</span>}
            <ProcessingJobAction job={job} permissions={permissions} onAccepted={onActionAccepted} />
          </div></td>
        </tr>;
      })}</tbody>
    </table></div>
  </div>;
}

function VideoProcessing({ media, permissions, onAccepted, onPage, onOpenVideo }: {
  media: AiOpsDashboardData["media"]; permissions: string[]; onAccepted: () => void;
  onPage: (page: number, pageSize: 25 | 50 | 100) => void;
  onOpenVideo: (sourceAssetId: string) => void;
}) {
  const recent = media?.recent_video;
  if (!recent?.items.length) return <DashboardState kind="empty" label="No video processing jobs in this period" />;
  const pages = Math.max(1, Math.ceil(recent.total / recent.page_size));
  const currentPage = Math.min(Math.max(1, recent.page), pages);
  const first = (currentPage - 1) * recent.page_size + 1;
  const last = Math.min(currentPage * recent.page_size, recent.total);
  return <div className="ops-content">
    <div className="ops-table-heading">
      <div><h2>Video processing jobs</h2><p>Showing {first}-{last} of {recent.total}</p></div>
      <div className="ops-pagination" aria-label="Video processing pagination">
        <label>Items per page<select aria-label="Video items per page" value={recent.page_size} onChange={event => onPage(1, Number(event.target.value) as 25 | 50 | 100)}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <nav aria-label="Video processing page numbers">
          <button type="button" aria-label="Previous video page" disabled={currentPage <= 1} onClick={() => onPage(currentPage - 1, recent.page_size as 25 | 50 | 100)}>Previous</button>
          {visiblePages(currentPage, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" aria-hidden="true" key={"ellipsis-" + index}>...</span> : <button type="button" key={entry} aria-label={"Video page " + entry} aria-current={entry === currentPage ? "page" : undefined} className={entry === currentPage ? "active" : ""} onClick={() => onPage(entry, recent.page_size as 25 | 50 | 100)}>{entry}</button>)}
          <button type="button" aria-label="Next video page" disabled={currentPage >= pages} onClick={() => onPage(currentPage + 1, recent.page_size as 25 | 50 | 100)}>Next</button>
        </nav>
      </div>
    </div>
    <ProcessingFailureGroupRetry
      failures={media?.analytics.failures || []}
      permissions={permissions}
      onAccepted={onAccepted}
      jobType="video_analyze"
    />
    <div className="ops-table-scroll"><table className="ops-data-table">
      <caption className="sr-only">Video processing jobs</caption>
      <thead><tr>{["Status", "Video", "Segments", "Attempts", "Updated", "Error", "Actions"].map(value => <th key={value}>{value}</th>)}</tr></thead>
      <tbody>{recent.items.map(job => <tr key={job.job_id}>
        <td><StatusText status={job.status} /></td>
        <td><div className="video-processing-title">
          <VideoThumbnailWithDuration thumbnailUrl={job.thumbnail_url} durationMs={job.duration_ms} />
          <span><button type="button" className="video-processing-title-button" onClick={() => onOpenVideo(job.source_asset_id)} aria-label={"Mở chi tiết " + (job.filename || job.source_asset_id)}>{job.filename || job.source_asset_id}</button><small>{job.location || job.source_asset_id}</small></span>
        </div></td>
        <td title="Completed processing segments / total segments">{job.total_chunks ? (job.completed_chunks || 0) + "/" + job.total_chunks : "—"}</td>
        <td>{job.attempt_count}/{job.max_attempts}</td>
        <td><time dateTime={job.updated_at}>{new Date(job.updated_at).toLocaleString()}</time></td>
        <td><ErrorDetailPopover code={job.error_code} message={job.error_message} /></td>
        <td><VideoProcessingJobRetry job={job} permissions={permissions} onAccepted={onAccepted} /></td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}

function VideoProcessingJobRetry({ job, permissions, onAccepted }: {
  job: NonNullable<AiOpsDashboardData["media"]>["recent_video"]["items"][number];
  permissions: string[];
  onAccepted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  if (job.status !== "failed" || !permissions.includes("ai_jobs.retry")) return null;
  async function submit() {
    if (!reason.trim()) return;
    setBusy(true); setMessage("");
    try {
      const result = await retryAiOperationsJob(job.job_id, reason.trim());
      setMessage(result.outcome.replaceAll("_", " "));
      setConfirming(false);
      setReason("");
      onAccepted();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Video retry failed");
    } finally {
      setBusy(false);
    }
  }
  return <>
    <button type="button" onClick={() => { setConfirming(true); setReason(""); setMessage(""); }}>Retry failed job</button>
    {confirming && <div className="ops-confirm ops-job-confirm" role="dialog" aria-modal="true" aria-label="Confirm video job retry">
      <strong>Retry failed video job</strong>
      <p>This action is audited. Enter a reason before continuing.</p>
      <label>Reason<input autoFocus value={reason} onChange={event => setReason(event.target.value)} /></label>
      <div><button type="button" onClick={() => setConfirming(false)}>Back</button><button type="button" className="danger" disabled={busy || !reason.trim()} onClick={submit}>{busy ? "Retrying..." : "Confirm retry"}</button></div>
    </div>}
    {message && <span className="ops-action-message" aria-live="polite">{message}</span>}
  </>;
}

export function formatVideoDuration(durationMs: number | null | undefined): string {
  if (!Number.isFinite(durationMs) || durationMs === null || durationMs === undefined || durationMs < 0) return "—";
  const totalSeconds = Math.round(durationMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatProcessingDuration(durationMs: number | null | undefined): string {
  if (!Number.isFinite(durationMs) || !durationMs || durationMs < 0) return "—";
  if (durationMs < 1000) return `${durationMs} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} s`;
  return `${(durationMs / 60_000).toFixed(1)} min`;
}
export type ProcessingJobActionKind = "retry" | "force_retry" | "cancel";

export function eligibleProcessingAction(job: AiOpsJob): ProcessingJobActionKind | null {
  if (job.is_deferred) return "force_retry";
  if (job.status === "failed" && !["operation_cancelled", "analysis_cancelled", "batch_cancelled"].includes(job.error?.code || "")) return "retry";
  if (["pending", "retry", "processing"].includes(job.status)) return "cancel";
  return null;
}

export function ProcessingJobAction({ job, permissions = [], onAccepted }: { job: AiOpsJob; permissions?: string[]; onAccepted: () => void }) {
  const action = eligibleProcessingAction(job);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const requiredPermission = action === "retry" || action === "force_retry" ? "ai_jobs.retry" : "ai_jobs.cancel";
  if (action && !permissions.includes(requiredPermission)) return null;
  if (!action) return null;
  const running = job.status === "processing";
  const label = action === "force_retry" ? "Force retry now" : action === "retry" ? "Retry failed job" : running ? "Request cancellation" : "Cancel queued job";

  async function submit() {
    if (!reason.trim()) return;
    setBusy(true); setMessage("");
    try {
      const result = action === "retry" || action === "force_retry"
        ? await retryAiOperationsJob(job.id, reason.trim(), action === "force_retry")
        : await cancelAiOperationsJob(job.id, reason.trim());
      setMessage(result.outcome.replaceAll("_", " "));
      setConfirming(false);
      onAccepted();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Job action failed");
    } finally {
      setBusy(false);
    }
  }

  return <>
    <button type="button" aria-label={`${label}: ${job.id}`} onClick={() => { setConfirming(true); setReason(""); setMessage(""); }}>{label}</button>
    {confirming && <div className="ops-confirm ops-job-confirm" role="dialog" aria-modal="true" aria-label={`Confirm ${label.toLowerCase()}`}>
      <strong>{label}</strong>
      <p>This action is audited. Enter a reason before continuing.</p>
      <label>Reason<input autoFocus value={reason} onChange={event => setReason(event.target.value)} /></label>
      <div><button type="button" onClick={() => setConfirming(false)}>Back</button><button type="button" className="danger" disabled={busy || !reason.trim()} onClick={submit}>Confirm {label.toLowerCase()}</button></div>
    </div>}
    {message && <span className="ops-action-message" aria-live="polite">{message}</span>}
  </>;
}

function CostUsage({ data, filters, onFilters }: { data: AiOpsDashboardData; filters: AiOpsFilters; onFilters: (value: AiOpsFilters) => void }) {
  if (!data.usage.items.length) return <DashboardState kind="empty" label="No usage records in this period" />;
  const pages = Math.max(1, Math.ceil(data.usage.total / data.usage.page_size));
  const currentPage = Math.min(Math.max(1, data.usage.page), pages);
  const firstAsset = (currentPage - 1) * data.usage.page_size + 1;
  const lastAsset = Math.min(currentPage * data.usage.page_size, data.usage.total);
  return <div className="ops-content">
    <div className="ops-table-heading">
      <div><h2>AI cost and usage records</h2><p>Showing {firstAsset}-{lastAsset} of {data.usage.total}</p></div>
      <div className="ops-pagination" aria-label="Cost and usage pagination">
        <label>Số mục mỗi trang<select aria-label="Usage items per page" value={data.usage.page_size} onChange={event => onFilters({ ...filters, usagePage: 1, usagePageSize: Number(event.target.value) as 25 | 50 | 100 })}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <nav aria-label="Cost and usage page numbers">
          <button type="button" aria-label="Trước usage page" disabled={currentPage <= 1} onClick={() => onFilters(usagePageFilters(filters, currentPage - 1))}>Trước</button>
          {visiblePages(currentPage, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" aria-hidden="true" key={`usage-ellipsis-${index}`}>...</span> : <button type="button" key={entry} aria-label={`Usage page ${entry}`} aria-current={entry === currentPage ? "page" : undefined} className={entry === currentPage ? "active" : ""} onClick={() => onFilters(usagePageFilters(filters, entry))}>{entry}</button>)}
          <button type="button" aria-label="Tiếp usage page" disabled={currentPage >= pages} onClick={() => onFilters(usagePageFilters(filters, currentPage + 1))}>Tiếp</button>
        </nav>
      </div>
    </div>
    {data.summary && <section className="ops-cost-summary" aria-label="Cost totals for selected period">
      <article><span>Estimated total</span><strong>{formatCost(data.summary.cost.estimated_cost_micros, data.summary.cost.currency)}</strong></article><article><span>Provider-reported total</span><strong>{formatCost(data.summary.cost.provider_reported_cost_micros, data.summary.cost.currency)}</strong></article><article><span>Reconciled total</span><strong>{formatCost(data.summary.cost.reconciled_cost_micros, data.summary.cost.currency)}</strong></article>
    </section>}
    <div className="ops-table-scroll"><table className="ops-data-table"><caption className="sr-only">AI cost and usage records</caption><thead><tr>{["Date", "Provider", "Model", "Mode", "Input units", "Output units", "Estimated cost", "Provider-reported cost"].map(value => <th key={value}>{value}</th>)}</tr></thead><tbody>{data.usage.items.map(item => <tr key={item.id}>
      <td><time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString()}</time></td><td>{providerLabel(item.provider)}</td><td>{item.model || "\u2014"}</td><td>{modeLabel(item.processing_mode)}</td><td>{item.input_units.toLocaleString()}</td><td>{item.output_units.toLocaleString()}</td><td>{formatCost(item.estimated_cost_micros, item.currency)}</td><td>{formatCost(item.provider_reported_cost_micros, item.currency)}</td>
    </tr>)}</tbody></table></div>
  </div>;
}

export function StatusText({ status, isDeferred = false, nextAttemptAt = null }: { status: string; isDeferred?: boolean; nextAttemptAt?: string | null }) {
  if (isDeferred) {
    const retry = nextAttemptAt ? ` Tiếp retry ${new Date(nextAttemptAt).toLocaleString()}.` : "";
    return <span className="ops-status waiting" aria-label={`Status: Đang chờ for Gemini quota.${retry}`}><i aria-hidden="true" />Đang chờ for Gemini quota{nextAttemptAt && <small> - {new Date(nextAttemptAt).toLocaleString()}</small>}</span>;
  }
  const label = status === "pending" ? "Đã xếp hàng" : status.replaceAll("_", " ").replace(/\b\w/g, value => value.toUpperCase());
  return <span className={`ops-status ${status}`} aria-label={`Status: ${label}`}><i aria-hidden="true" />{label}</span>;
}

export function DashboardSkeleton() {
  return <div className="ops-skeleton" aria-busy="true" aria-label="Loading AI Operations dashboard"><i /><i /><i /><i /><span>Loading AI Operations…</span></div>;
}

export function DashboardState({ kind, label, onRetry }: { kind: "empty" | "unauthorized"; label?: string; onRetry?: () => void }) {
  return <div className={`ops-state ${kind}`} role={kind === "unauthorized" ? "alert" : "status"}><strong>{kind === "unauthorized" ? "AI Operations access required" : label || "No AI activity in this period"}</strong><p>{kind === "unauthorized" ? (label || "Sign in with an authorized account that has ai_operations.read.") : "Try a wider date range or clear one of the filters."}</p>{onRetry && <button type="button" onClick={onRetry}>Retry</button>}</div>;
}

function SearchCoverageCard({ coverage, canManage, onRefresh }: { coverage: AiOpsSearchCoverage | null | undefined; canManage: boolean; onRefresh: () => void }) {
  const [busy, setBusy] = useState<"audit" | "repair" | null>(null);
  const [message, setMessage] = useState("");
  const activeRepair = Boolean(coverage && (coverage.repair_jobs.queued || coverage.repair_jobs.running));
  useEffect(() => {
    if (!activeRepair) return;
    const timer = window.setInterval(onRefresh, 10_000);
    return () => window.clearInterval(timer);
  }, [activeRepair, onRefresh]);
  if (!coverage) return null;
  const run = async (kind: "audit" | "repair") => {
    if (kind === "repair" && !window.confirm("Repair missing search data? This queues only projection and index jobs; it never runs AI.")) return;
    setBusy(kind); setMessage("");
    try {
      if (kind === "audit") await runSearchCoverageAudit({ verify_elasticsearch: true, limit: 100 });
      else await repairSearchCoverage({ confirmed: true, limit: 100, verify_elasticsearch: true, repair_projections: true, repair_indexes: true });
      setMessage(kind === "audit" ? "Coverage audit completed." : "Repair jobs queued.");
      onRefresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Search coverage action failed");
    } finally { setBusy(null); }
  };
  return <section className="ops-coverage" aria-label="Search Coverage">
    <div><h2>Search Coverage</h2><p>Database metrics are fast. Elasticsearch verification runs only when an administrator requests an audit.</p></div>
    <dl><div><dt>Analyzed</dt><dd>{coverage.completed_analysis_assets}</dd></div><div><dt>Projected</dt><dd>{coverage.current_projection_assets}</dd></div><div><dt>Indexed</dt><dd>{coverage.v3_indexed_documents}</dd></div><div><dt>Missing</dt><dd>{coverage.projection_missing + coverage.projection_stale + coverage.indexing_backlog}</dd></div><div><dt>Coverage</dt><dd>{coverage.coverage_percent.toFixed(1)}%</dd></div></dl>
    {coverage.database_indexed_document_missing > 0 && <p role="alert">Database and Elasticsearch disagree for {coverage.database_indexed_document_missing} asset(s). Run repair after reviewing the audit.</p>}
    <p>Last audit: {coverage.last_audited_at ? new Date(coverage.last_audited_at).toLocaleString() : "Not run"}{coverage.elasticsearch_verification_included ? " (Elasticsearch verified)" : ""}. Repair queue: {coverage.repair_jobs.queued} queued, {coverage.repair_jobs.running} running.</p>
    {canManage && <div className="ops-coverage-actions"><button type="button" disabled={busy !== null} onClick={() => void run("audit")}>{busy === "audit" ? "Đang chạy audit..." : "Run coverage audit"}</button><button type="button" className="danger" disabled={busy !== null} onClick={() => void run("repair")}>{busy === "repair" ? "Queuing repair..." : "Repair missing search data"}</button></div>}
    {message && <p aria-live="polite">{message}</p>}
  </section>;
}
