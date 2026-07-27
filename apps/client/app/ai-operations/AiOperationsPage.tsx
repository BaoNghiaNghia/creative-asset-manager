import { useEffect, useMemo, useRef, useState } from "react";
import {
  aiOperationsExportUrl, cancelAiOperationsJob, fetchAiOperationsDashboard, filtersFromSearch, repairSearchCoverage, runSearchCoverageAudit,
  retryAiOperationsJob, searchFromFilters,
  type AiOpsDashboardData, type AiOpsFilters, type AiOpsJob, type AiOpsUsage, type AiOpsSearchCoverage, type PipelineSnapshot,
} from "../../features/ai_operations";
import { AccessibleChart } from "./AccessibleChart";
import { fetchAccessIdentity, type AccessIdentity } from "../../features/access_management";
import { ConfigurationTab, ProvidersTab } from "./ProvidersConfiguration";
import {
  dailyProviderCostChart, dailyStatusChart, failureChart,
  formatCost, formatDuration, modeLabel, providerLabel, providerVolumeChart,
} from "./presentation";
import {
  AUTO_REFRESH_SECONDS, DashboardRequestCoordinator, autoRefreshFromSearch,
  shouldAutoRefresh, type AutoRefreshSeconds,
} from "./requestCoordinator";

export type AiOpsTab = "pipeline" | "overview" | "processing" | "cost" | "providers" | "configuration";
const tabs: Array<{ id: AiOpsTab; label: string }> = [
  { id: "pipeline", label: "Pipeline overview" },
  { id: "overview", label: "AI analysis" },
  { id: "processing", label: "Processing" },
  { id: "cost", label: "Cost & Usage" },
  { id: "providers", label: "Providers" },
  { id: "configuration", label: "Configuration" },
];

const emptyPage = <T,>(page = 1) => ({ page, page_size: 25, total: 0, items: [] as T[] });
export const emptyDashboard = (page = 1): AiOpsDashboardData => ({
  summary: null, today: null, month: null, daily: [], providers: [], todayProviders: [], failures: [],
  jobs: emptyPage<AiOpsJob>(page), usage: emptyPage<AiOpsUsage>(), coverage: null, pipeline: null,
});

export function AiOperationsPage() {
  const [filters, setFilters] = useState(() => filtersFromSearch(window.location.search));
  const initialTab = new URLSearchParams(window.location.search).get("tab") as AiOpsTab | null;
  const [tab, setTab] = useState<AiOpsTab>(tabs.some(item => item.id === initialTab) ? initialTab! : "overview");
  const [refreshSeconds, setRefreshSeconds] = useState<AutoRefreshSeconds>(() => autoRefreshFromSearch(window.location.search));
  const [data, setData] = useState<AiOpsDashboardData>(() => emptyDashboard(filters.page));
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [unauthorized, setUnauthorized] = useState(false);
  const [identity, setIdentity] = useState<AccessIdentity | null>(null);
  const [authorizationReason, setAuthorizationReason] = useState("Sign in is required.");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [reload, setReload] = useState(0);
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
      filters, (url, init) => fetch(url, { ...init, signal }),
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
    updateUrl(next, tab, refreshSeconds);
  }
  function changeTab(next: AiOpsTab) {
    setTab(next);
    updateUrl(filters, next, refreshSeconds);
  }
  function changeRefresh(next: AutoRefreshSeconds) {
    setRefreshSeconds(next);
    updateUrl(filters, tab, next);
  }
  return <AiOperationsShell>
    <AiOperationsContent
      data={data} loading={loading} errors={errors} unauthorized={unauthorized}
      filters={filters} tab={tab} onTab={changeTab} onFilters={changeFilters}
      refreshSeconds={refreshSeconds} onRefreshSeconds={changeRefresh}
      lastUpdated={lastUpdated}
      permissions={identity?.permissions || []}
      authorizationReason={authorizationReason}
      onRetry={() => setReload(value => value + 1)}
    />
  </AiOperationsShell>;
}

function updateUrl(filters: AiOpsFilters, tab: AiOpsTab, refreshSeconds: AutoRefreshSeconds) {
  const query = searchFromFilters(filters, tab, refreshSeconds);
  window.history.replaceState({}, "", `/ai-operations${query ? `?${query}` : ""}`);
}

export function AiOperationsShell({ children }: { children: React.ReactNode }) {
  return <main className="ops-shell">
    <aside className="ops-sidebar">
      <div className="brand"><b>C</b><span><strong>Creative assets</strong><small>Operations console</small></span></div>
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
};

export function AiOperationsContent({
  data, filters, tab, loading = false, errors = [], unauthorized = false,
  onTab, onFilters, onRetry, refreshSeconds = 0, onRefreshSeconds = () => undefined,
  lastUpdated = null, permissions = [], authorizationReason = "Sign in is required.",
}: ContentProps) {
  const models = useMemo(() => [...new Set([
    ...data.providers.map(item => item.model || ""), ...data.usage.items.map(item => item.model || ""),
  ].filter(Boolean))].sort(), [data]);
  const profiles = useMemo(() => [...new Set(data.usage.items.map(item => item.metadata_profile || "").filter(Boolean))].sort(), [data]);
  if (unauthorized) return <DashboardState kind="unauthorized" label={authorizationReason} onRetry={onRetry} />;
  return <>
    <header className="ops-header">
      <div><small>OPERATIONS</small><h1>Processing Operations</h1><p>Pipeline progress, AI analysis, usage and cost for the current tenant.</p></div>
      <div className="ops-header-actions">
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
      {tabs.map(item => <button key={item.id} id={`ops-tab-${item.id}`} type="button" role="tab" aria-selected={tab === item.id} aria-controls={`ops-panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1} className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}>{item.label}</button>)}
    </nav>
    <div className="ops-query-bar">
      <AiOperationsFilters filters={filters} models={models} profiles={profiles} onChange={onFilters} />
      <details className="ops-export-menu">
        <summary>Export data</summary>
        <nav aria-label="AI Operations CSV exports">
          {(["daily", "usage", "failures", "jobs"] as const).map(kind => <a key={kind} href={aiOperationsExportUrl(kind, filters)}>Export {kind} CSV</a>)}
        </nav>
      </details>
    </div>
    {errors.length > 0 && <div className="ops-partial-error" role="alert" aria-live="assertive">
      <div><b>Some dashboard data could not be loaded.</b><span>{errors.join(" · ")}</span></div>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>}
    <section id={`ops-panel-${tab}`} role="tabpanel" aria-labelledby={`ops-tab-${tab}`} tabIndex={0}>
      {loading ? <DashboardSkeleton /> : tab === "pipeline" ? <PipelineOverview pipeline={data.pipeline} onPage={(page, pageSize) => onFilters({ ...filters, pipelinePage: page, pipelinePageSize: pageSize })} />
        : tab === "overview" ? <Overview data={data} canManage={permissions.includes("search.rebuild")} onRefresh={onRetry} />
        : tab === "processing" ? <Processing data={data} filters={filters} permissions={permissions} onFilters={onFilters} onActionAccepted={onRetry} />
        : tab === "cost" ? <CostUsage data={data} filters={filters} onFilters={onFilters} />
        : tab === "providers" ? <ProvidersTab metrics={data.todayProviders} />
        : <ConfigurationTab />}
    </section>
  </>;
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
  const field = (changes: Partial<AiOpsFilters>) => onChange({ ...filters, ...changes, page: 1, usagePage: 1 });
  return <form className="ops-filters" aria-label="Dashboard filters" onSubmit={event => event.preventDefault()}>
    <label>Date range<select aria-label="Date range" value={filters.range} onChange={event => field({ range: Number(event.target.value) as 7 | 30 | 90 })}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label>
    <label>Provider<select aria-label="Provider" value={filters.provider} onChange={event => field({ provider: event.target.value })}><option value="">All providers</option><option value="gemini">Google Gemini</option><option value="openai">OpenAI</option></select></label>
    <label>Model<input aria-label="Model" list="ops-models" value={filters.model} onChange={event => field({ model: event.target.value })} placeholder="All models" /><datalist id="ops-models">{models.map(model => <option key={model} value={model} />)}</datalist></label>
    <label>Mode<select aria-label="Processing mode" value={filters.processingMode} onChange={event => field({ processingMode: event.target.value })}><option value="">All modes</option><option value="single">Single</option><option value="batch">Batch</option></select></label>
    <label>Metadata profile<input aria-label="Metadata profile" list="ops-profiles" value={filters.metadataProfile} onChange={event => field({ metadataProfile: event.target.value })} placeholder="All profiles" /><datalist id="ops-profiles">{profiles.map(profile => <option key={profile} value={profile} />)}</datalist></label>
    <label>Status<select aria-label="Processing status" value={filters.status} onChange={event => field({ status: event.target.value })}><option value="">All statuses</option><option value="waiting">Waiting</option><option value="queued">Queued</option><option value="running">Running</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></select></label>
  </form>;
}

export function PipelineOverview({ pipeline, onPage = () => undefined }: { pipeline?: PipelineSnapshot | null; onPage?: (page: number, pageSize: 25 | 50 | 100) => void }) {
  if (pipeline === undefined) return <DashboardState kind="empty" label="Pipeline overview is unavailable until the API is updated and restarted." />;
  if (pipeline === null) return <DashboardState kind="empty" label="No pipeline activity for this tenant" />;
  const scan = pipeline.latest_source_sync;
  const active = pipeline.active_job;
  const flowStages = [{
    key: "source_sync", label: "Google Drive Scan",
    completed: scan?.status === "completed" ? 1 : 0,
    pending: scan?.status === "running" ? 1 : 0,
    processing: scan?.status === "running" ? 1 : 0,
    failed: scan?.status === "failed" ? 1 : 0,
  }, ...pipeline.stages];
  return <div className="ops-content pipeline-content">
    <section className="pipeline-summary" aria-label="Pipeline summary">
      <PipelineMetric label="Supported assets" value={pipeline.overall.supported_assets} detail="Images eligible for processing" />
      <PipelineMetric label="Indexed" value={pipeline.overall.completed} detail={pipeline.overall.indexed_percentage === null ? "Calculating progress" : String(pipeline.overall.indexed_percentage) + "% complete"} />
      <PipelineMetric label="Active" value={pipeline.overall.active} detail="Currently moving through the pipeline" />
      <PipelineMetric label="Queued" value={pipeline.overall.queued} detail="Waiting to start" />
      <PipelineMetric label="Needs attention" value={pipeline.overall.failed} detail="Current unresolved failures" tone="attention" />
      <PipelineMetric label="Unsupported" value={pipeline.overall.unsupported_assets} detail="Excluded from image processing" />
    </section>
    <section className="pipeline-scan-card" aria-label="Latest Google Drive scan">
      <div><small>GOOGLE DRIVE SCAN</small><h2>{scan ? "Last " + scan.mode + " scan" : "No scan recorded"}</h2>
      <p>{scan ? (scan.status === "completed" ? "Found " + scan.items_seen_count.toLocaleString() + " Drive items and created " + scan.jobs_created_count.toLocaleString() + " processing jobs." : "Scan is " + scan.status + ". " + scan.items_seen_count.toLocaleString() + " items seen so far.") : "Run a source sync to discover Drive assets."}</p></div>
      {scan && <dl><div><dt>Status</dt><dd><StatusText status={scan.status} /></dd></div><div><dt>Pages</dt><dd>{scan.pages_count}</dd></div><div><dt>Items</dt><dd>{scan.items_seen_count.toLocaleString()}</dd></div><div><dt>Jobs created</dt><dd>{scan.jobs_created_count.toLocaleString()}</dd></div><div><dt>Completed</dt><dd>{scan.completed_at ? new Date(scan.completed_at).toLocaleString() : "-"}</dd></div><div><dt>Duration</dt><dd>{formatProcessingDuration(scan.duration_ms)}</dd></div></dl>}
    </section>
    <section className="pipeline-progress-summary" aria-label="Asset progress by furthest completed stage"><div><small>ASSET PROGRESS</small><h2>Furthest completed stage</h2><p>Each supported source asset appears once, at its latest completed stage.</p></div><dl>{pipeline.overall.asset_progress.map(item => <div key={item.key}><dt>{item.key.replaceAll("_", " ")}</dt><dd>{item.count.toLocaleString()}</dd></div>)}</dl></section>
    <ol className="pipeline-flow" aria-label="Google Drive asset processing flow">{flowStages.map(stage => <li key={stage.key} className={stage.key === active?.job_type ? "processing" : stage.failed ? "failed" : stage.processing ? "processing" : stage.pending ? "pending" : stage.completed ? "completed" : "idle"}><b>{stage.label}</b><span>{stage.completed} completed</span><small>{stage.pending} pending / {stage.processing} running / {stage.failed} failed</small></li>)}</ol>
    <section className="pipeline-stage-grid" aria-label="Pipeline stages">{pipeline.stages.map(stage => <article key={stage.key} className={stage.key === active?.job_type ? "active" : ""}>
      <header><div><small>STAGE</small><h2>{stage.label}</h2></div><StatusText status={stage.processing ? "processing" : stage.failed ? "failed" : stage.pending ? "pending" : stage.completed ? "completed" : "idle"} /></header><p>{stage.subtitle}</p>
      <strong>{stage.completed} / {stage.total || "-"}</strong><span>{stage.percentage === null ? "Calculating progress" : String(stage.percentage) + "% complete"}</span><div className="pipeline-progress"><i style={{ width: String(stage.percentage || 0) + "%" }} /></div>
      <dl><div><dt>Queued</dt><dd>{stage.pending}</dd></div><div><dt>Running</dt><dd>{stage.processing}</dd></div><div><dt>Waiting</dt><dd>{stage.waiting}</dd></div><div><dt>Failed</dt><dd>{stage.failed}</dd></div></dl>
    </article>)}</section>
    <section className="pipeline-active-job"><div><small>CURRENTLY PROCESSING</small><h2>{active ? active.stage : "No job is currently processing"}</h2><p>{active ? active.message : "The worker will claim the next eligible job automatically."}</p></div>{active && <dl><div><dt>Item</dt><dd>{active.filename || "Pipeline item"}</dd></div><div><dt>Started</dt><dd>{active.started_at ? new Date(active.started_at).toLocaleString() : "-"}</dd></div><div><dt>Elapsed</dt><dd>{formatProcessingDuration(active.elapsed_ms)}</dd></div><div><dt>Attempt</dt><dd>{active.attempt_count}/{active.max_attempts}</dd></div></dl>}</section>
    <section className="pipeline-queue"><h2>Queue by stage</h2><div className="ops-table-scroll"><table className="ops-data-table"><thead><tr><th>Stage</th><th>Pending</th><th>Eligible now</th><th>Waiting</th><th>Processing</th><th>Completed</th><th>Failed</th></tr></thead><tbody>{pipeline.stages.map(stage => <tr key={stage.key}><td>{stage.label}</td><td>{stage.pending}</td><td>{stage.eligible_now}</td><td>{stage.waiting}</td><td>{stage.processing}</td><td>{stage.completed}</td><td>{stage.failed}</td></tr>)}</tbody></table></div></section>
    <section className="pipeline-attention"><h2>Needs attention</h2>{pipeline.failure_groups.length ? <ul>{pipeline.failure_groups.map(item => <li key={item.stage + item.error_code}><b>{item.stage}</b><code>{item.error_code}</code><span>{item.count} affected / {new Date(item.latest_at).toLocaleString()}</span></li>)}</ul> : <p>No current unresolved pipeline failures.</p>}</section>
    <PipelineRecentAssets recent={pipeline.recent_assets} onPage={onPage} />
  </div>;
}
function PipelineRecentAssets({ recent, onPage }: { recent: PipelineSnapshot["recent_assets"]; onPage: (page: number, pageSize: 25 | 50 | 100) => void }) {
  if (!recent.items.length) return <section className="pipeline-recent"><h2>Recent asset progress</h2><p>No pipeline assets have been created yet.</p></section>;
  const pages = Math.max(1, Math.ceil(recent.total / recent.page_size));
  const page = Math.min(recent.page, pages);
  const first = (page - 1) * recent.page_size + 1;
  const last = Math.min(page * recent.page_size, recent.total);
  return <section className="pipeline-recent"><div className="ops-table-heading"><div><h2>Recent asset progress</h2><p>Showing {first}-{last} of {recent.total} logical assets. Select a name to open details.</p></div><div className="ops-pagination" aria-label="Pipeline asset pagination"><label>Items per page<select aria-label="Pipeline items per page" value={recent.page_size} onChange={event => onPage(1, Number(event.target.value) as 25 | 50 | 100)}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label><nav aria-label="Pipeline asset page numbers"><button type="button" disabled={page <= 1} onClick={() => onPage(page - 1, recent.page_size as 25 | 50 | 100)}>Previous</button>{visiblePages(page, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" key={"pipeline-ellipsis-" + index}>...</span> : <button type="button" key={entry} className={entry === page ? "active" : ""} aria-current={entry === page ? "page" : undefined} onClick={() => onPage(entry, recent.page_size as 25 | 50 | 100)}>{entry}</button>)}<button type="button" disabled={page >= pages} onClick={() => onPage(page + 1, recent.page_size as 25 | 50 | 100)}>Next</button></nav></div></div><div className="ops-table-scroll"><table className="ops-data-table"><thead><tr><th>Asset</th><th>Current stage</th><th>Download</th><th>Store</th><th>AI</th><th>Projection</th><th>Index</th><th>Updated</th><th>Attention</th></tr></thead><tbody>{recent.items.map(item => <tr key={item.asset_id || item.filename}><td>{item.asset_id ? <a href={"/?details=1&asset=" + encodeURIComponent(item.asset_id)}>{item.filename}</a> : item.filename}</td><td>{item.state.replaceAll("_", " ")}</td>{(["download", "store", "analyze", "projection", "index"] as const).map(stage => <td key={stage}><StatusText status={item.stage_statuses[stage] || "not_started"} /></td>)}<td>{new Date(item.updated_at).toLocaleString()}</td><td>{item.error_code || "-"}</td></tr>)}</tbody></table></div></section>;
}

function PipelineMetric({ label, value, detail, tone = "" }: { label: string; value: number; detail: string; tone?: string }) {
  return <article className={tone}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function Overview({ data, canManage, onRefresh }: { data: AiOpsDashboardData; canManage: boolean; onRefresh: () => void }) {
  const summary = data.summary;
  if (!summary && !data.daily.length) return <DashboardState kind="empty" />;
  const processedToday = (data.today?.completed || 0) + (data.today?.failed || 0);
  const cards = [
    { label: "Processed today", value: processedToday, detail: "Completed and failed today", tone: "neutral" },
    { label: "Completed", value: summary?.completed || 0, detail: "Finished successfully", tone: "success" },
    { label: "Failed", value: summary?.failed || 0, detail: "Needs attention", tone: "danger" },
    { label: "Budget blocked", value: summary?.budget_blocked || 0, detail: "Stopped by budget policy", tone: "danger" },
    { label: "Waiting for quota", value: summary?.deferred || 0, detail: "Will retry automatically", tone: "warning" },
    { label: "Running", value: summary?.running || 0, detail: "Currently processing", tone: "info" },
    { label: "Queued", value: summary?.queued || 0, detail: "Waiting to start", tone: "neutral" },
    { label: "Success rate", value: `${((summary?.success_rate || 0) * 100).toFixed(1)}%`, detail: "Completed out of terminal jobs", tone: "success" },
    { label: "Estimated cost today", value: formatCost(data.today?.cost?.estimated_cost_micros, data.today?.cost?.currency), detail: "Projected usage for today", tone: "neutral" },
    { label: "Estimated cost this month", value: formatCost(data.month?.cost?.estimated_cost_micros, data.month?.cost?.currency), detail: "Projected monthly usage", tone: "neutral" },
  ];
  const nextQuotaRetry = summary?.next_deferred_retry_at;
  return <div className="ops-content">
    <p className="ops-ai-scope-note">These metrics cover AI analysis only. Download, storage, projection, and indexing are shown in Pipeline Overview.</p>
    <SearchCoverageCard coverage={data.coverage} canManage={canManage} onRefresh={onRefresh} />
    {nextQuotaRetry && <section className="ops-quota-notice" role="status" aria-label="Gemini quota retry status">
      <div><span className="ops-quota-badge">Quota</span><div><strong>Gemini quota is temporarily busy</strong><p>{summary?.deferred || 0} {summary?.deferred === 1 ? "analysis" : "analyses"} will retry automatically. No action is needed unless this keeps recurring.</p></div></div>
      <time dateTime={nextQuotaRetry}><span>Next retry</span>{new Date(nextQuotaRetry).toLocaleString()}</time>
    </section>}
    <section className="ops-kpis" aria-label="AI processing summary">{cards.map(card => <article key={card.label} className={`ops-kpi ops-kpi-${card.tone}`}><span>{card.label}</span><strong>{card.value}</strong><small>{card.detail}</small></article>)}</section>
    <section className="ops-charts">
      <AccessibleChart title="Daily processing" description="Completed and failed analyses by UTC day." data={dailyStatusChart(data.daily)} />
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

function Processing({ data, filters, permissions, onFilters, onActionAccepted }: { data: AiOpsDashboardData; filters: AiOpsFilters; permissions: string[]; onFilters: (value: AiOpsFilters) => void; onActionAccepted: () => void }) {
  const usageByJob = new Map(data.usage.items.filter(item => item.job_id).map(item => [item.job_id!, item]));
  if (!data.jobs.items.length) return <DashboardState kind="empty" label="No processing jobs in this period" />;
  const pages = Math.max(1, Math.ceil(data.jobs.total / data.jobs.page_size));
  const currentPage = Math.min(Math.max(1, data.jobs.page), pages);
  const firstItem = (currentPage - 1) * data.jobs.page_size + 1;
  const lastItem = Math.min(currentPage * data.jobs.page_size, data.jobs.total);
  return <div className="ops-content">
    <div className="ops-table-heading">
      <div><h2>AI processing jobs</h2><p>Showing {firstItem}-{lastItem} of {data.jobs.total}</p></div>
      <div className="ops-pagination" aria-label="Processing pagination">
        <label>Items per page<select aria-label="Items per page" value={data.jobs.page_size} onChange={event => onFilters({ ...filters, page: 1, pageSize: Number(event.target.value) as 25 | 50 | 100 })}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <nav aria-label="Processing page numbers">
          <button type="button" aria-label="Previous page" disabled={currentPage <= 1} onClick={() => onFilters(pageFilters(filters, currentPage - 1))}>Previous</button>
          {visiblePages(currentPage, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" aria-hidden="true" key={`ellipsis-${index}`}>...</span> : <button type="button" key={entry} aria-label={`Page ${entry}`} aria-current={entry === currentPage ? "page" : undefined} className={entry === currentPage ? "active" : ""} onClick={() => onFilters(pageFilters(filters, entry))}>{entry}</button>)}
          <button type="button" aria-label="Next page" disabled={currentPage >= pages} onClick={() => onFilters(pageFilters(filters, currentPage + 1))}>Next</button>
        </nav>
      </div>
    </div>
    <div className="ops-table-scroll"><table className="ops-data-table">
      <caption className="sr-only">AI processing jobs</caption>
      <thead><tr>{["Status", "Asset", "Provider", "Model", "Mode", "Profile", "Attempts", "Duration", "Cost", "Error", "Actions"].map(value => <th key={value}>{value}</th>)}</tr></thead>
      <tbody>{data.jobs.items.map(job => {
        const usage = usageByJob.get(job.id);
        const mode = usage?.processing_mode || (job.job_type.startsWith("ai_batch_") ? "batch" : "single");
        const assetId = job.asset_id || usage?.asset_id || (job.entity_type === "asset" ? job.entity_id : null);
        return <tr key={job.id}>
          <td><StatusText status={job.status} isDeferred={job.is_deferred} nextAttemptAt={job.next_attempt_at} /></td><td><code>{assetId || "\u2014"}</code></td>
          <td>{providerLabel(job.provider)}</td><td>{usage?.model || "\u2014"}</td><td>{modeLabel(mode)}</td>
          <td>{usage?.metadata_profile || "\u2014"}</td><td>{job.attempt_count}/{job.max_attempts}</td>
          <td>{job.status === "processing" ? formatDuration(job.claimed_at, job.updated_at) : formatProcessingDuration(job.processing_duration_ms)}</td>
          <td>{formatCost(usage?.estimated_cost_micros, usage?.currency)}</td><td><code>{job.error?.code || "\u2014"}</code></td>
          <td><div className="ops-job-actions">
            {assetId ? <a aria-label={`View asset ${assetId}`} href={`/?details=1&asset=${encodeURIComponent(assetId)}`}>View</a> : <span title="Asset identity is not available yet">Unavailable</span>}
            <ProcessingJobAction job={job} permissions={permissions} onAccepted={onActionAccepted} />
          </div></td>
        </tr>;
      })}</tbody>
    </table></div>
  </div>;
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
  const firstItem = (currentPage - 1) * data.usage.page_size + 1;
  const lastItem = Math.min(currentPage * data.usage.page_size, data.usage.total);
  return <div className="ops-content">
    <div className="ops-table-heading">
      <div><h2>AI cost and usage records</h2><p>Showing {firstItem}-{lastItem} of {data.usage.total}</p></div>
      <div className="ops-pagination" aria-label="Cost and usage pagination">
        <label>Items per page<select aria-label="Usage items per page" value={data.usage.page_size} onChange={event => onFilters({ ...filters, usagePage: 1, usagePageSize: Number(event.target.value) as 25 | 50 | 100 })}>{[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
        <nav aria-label="Cost and usage page numbers">
          <button type="button" aria-label="Previous usage page" disabled={currentPage <= 1} onClick={() => onFilters(usagePageFilters(filters, currentPage - 1))}>Previous</button>
          {visiblePages(currentPage, pages).map((entry, index) => entry === "ellipsis" ? <span className="ops-page-ellipsis" aria-hidden="true" key={`usage-ellipsis-${index}`}>...</span> : <button type="button" key={entry} aria-label={`Usage page ${entry}`} aria-current={entry === currentPage ? "page" : undefined} className={entry === currentPage ? "active" : ""} onClick={() => onFilters(usagePageFilters(filters, entry))}>{entry}</button>)}
          <button type="button" aria-label="Next usage page" disabled={currentPage >= pages} onClick={() => onFilters(usagePageFilters(filters, currentPage + 1))}>Next</button>
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
    const retry = nextAttemptAt ? ` Next retry ${new Date(nextAttemptAt).toLocaleString()}.` : "";
    return <span className="ops-status waiting" aria-label={`Status: Waiting for Gemini quota.${retry}`}><i aria-hidden="true" />Waiting for Gemini quota{nextAttemptAt && <small> - {new Date(nextAttemptAt).toLocaleString()}</small>}</span>;
  }
  const label = status === "pending" ? "Queued" : status.replaceAll("_", " ").replace(/\b\w/g, value => value.toUpperCase());
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
    {canManage && <div className="ops-coverage-actions"><button type="button" disabled={busy !== null} onClick={() => void run("audit")}>{busy === "audit" ? "Running audit..." : "Run coverage audit"}</button><button type="button" className="danger" disabled={busy !== null} onClick={() => void run("repair")}>{busy === "repair" ? "Queuing repair..." : "Repair missing search data"}</button></div>}
    {message && <p aria-live="polite">{message}</p>}
  </section>;
}
