import { useEffect, useMemo, useRef, useState } from "react";
import {
  aiOperationsExportUrl, cancelAiOperationsJob, fetchAiOperationsDashboard, filtersFromSearch,
  retryAiOperationsJob, searchFromFilters,
  type AiOpsDashboardData, type AiOpsFilters, type AiOpsJob, type AiOpsUsage,
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

export type AiOpsTab = "overview" | "processing" | "cost" | "providers" | "configuration";
const tabs: Array<{ id: AiOpsTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "processing", label: "Processing" },
  { id: "cost", label: "Cost & Usage" },
  { id: "providers", label: "Providers" },
  { id: "configuration", label: "Configuration" },
];

const emptyPage = <T,>(page = 1) => ({ page, page_size: 25, total: 0, items: [] as T[] });
export const emptyDashboard = (page = 1): AiOpsDashboardData => ({
  summary: null, today: null, month: null, daily: [], providers: [], todayProviders: [], failures: [],
  jobs: emptyPage<AiOpsJob>(page), usage: emptyPage<AiOpsUsage>(),
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
      <div><small>OPERATIONS</small><h1>AI Operations</h1><p>Processing health, usage and cost for the current tenant.</p></div>
      <div className="ops-header-actions">
        <label>Auto-refresh<select aria-label="Auto-refresh interval" value={refreshSeconds} onChange={event => onRefreshSeconds(Number(event.target.value) as AutoRefreshSeconds)}>
          {AUTO_REFRESH_SECONDS.map(seconds => <option key={seconds} value={seconds}>{seconds ? `${seconds}s` : "Off"}</option>)}
        </select></label>
        <span className="ops-refresh-status" aria-live="polite">{loading ? "Refreshing dashboard…" : lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : "Auto-refresh off"}</span>
        <a href="/">Back to assets</a>
      </div>
    </header>
    <nav className="ops-tabs" aria-label="AI Operations sections" role="tablist" onKeyDown={event => handleTabKeyDown(event, tab, onTab)}>
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
      {loading ? <DashboardSkeleton /> : tab === "overview" ? <Overview data={data} />
        : tab === "processing" ? <Processing data={data} filters={filters} permissions={permissions} onFilters={onFilters} onActionAccepted={onRetry} />
        : tab === "cost" ? <CostUsage data={data} filters={filters} />
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
  const field = (changes: Partial<AiOpsFilters>) => onChange({ ...filters, ...changes, page: 1 });
  return <form className="ops-filters" aria-label="Dashboard filters" onSubmit={event => event.preventDefault()}>
    <label>Date range<select aria-label="Date range" value={filters.range} onChange={event => field({ range: Number(event.target.value) as 7 | 30 | 90 })}><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label>
    <label>Provider<select aria-label="Provider" value={filters.provider} onChange={event => field({ provider: event.target.value })}><option value="">All providers</option><option value="gemini">Google Gemini</option><option value="openai">OpenAI</option></select></label>
    <label>Model<input aria-label="Model" list="ops-models" value={filters.model} onChange={event => field({ model: event.target.value })} placeholder="All models" /><datalist id="ops-models">{models.map(model => <option key={model} value={model} />)}</datalist></label>
    <label>Mode<select aria-label="Processing mode" value={filters.processingMode} onChange={event => field({ processingMode: event.target.value })}><option value="">All modes</option><option value="single">Single</option><option value="batch">Batch</option></select></label>
    <label>Metadata profile<input aria-label="Metadata profile" list="ops-profiles" value={filters.metadataProfile} onChange={event => field({ metadataProfile: event.target.value })} placeholder="All profiles" /><datalist id="ops-profiles">{profiles.map(profile => <option key={profile} value={profile} />)}</datalist></label>
  </form>;
}

function Overview({ data }: { data: AiOpsDashboardData }) {
  const summary = data.summary;
  if (!summary && !data.daily.length) return <DashboardState kind="empty" />;
  const processedToday = (data.today?.completed || 0) + (data.today?.failed || 0);
  const cards = [
    ["Processed today", processedToday], ["Completed", summary?.completed || 0],
    ["Failed", summary?.failed || 0], ["Budget blocked", summary?.budget_blocked || 0],
    ["Running", summary?.running || 0], ["Queued", summary?.queued || 0], ["Success rate", `${((summary?.success_rate || 0) * 100).toFixed(1)}%`],
    ["Estimated cost today", formatCost(data.today?.cost.estimated_cost_micros, data.today?.cost.currency)],
    ["Estimated cost this month", formatCost(data.month?.cost.estimated_cost_micros, data.month?.cost.currency)],
  ];
  return <div className="ops-content">
    <section className="ops-kpis" aria-label="AI processing summary">{cards.map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
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

function Processing({ data, filters, permissions, onFilters, onActionAccepted }: { data: AiOpsDashboardData; filters: AiOpsFilters; permissions: string[]; onFilters: (value: AiOpsFilters) => void; onActionAccepted: () => void }) {
  const usageByJob = new Map(data.usage.items.filter(item => item.job_id).map(item => [item.job_id!, item]));
  if (!data.jobs.items.length) return <DashboardState kind="empty" label="No processing jobs in this period" />;
  const pages = Math.max(1, Math.ceil(data.jobs.total / data.jobs.page_size));
  return <div className="ops-content">
    <div className="ops-table-scroll"><table className="ops-data-table">
      <caption>AI processing jobs</caption>
      <thead><tr>{["Status", "Asset", "Provider", "Model", "Mode", "Profile", "Attempts", "Duration", "Cost", "Error", "Actions"].map(value => <th key={value}>{value}</th>)}</tr></thead>
      <tbody>{data.jobs.items.map(job => {
        const usage = usageByJob.get(job.id);
        const mode = usage?.processing_mode || (job.job_type.startsWith("ai_batch_") ? "batch" : "single");
        const assetId = usage?.asset_id || (job.entity_type === "asset" ? job.entity_id : null);
        return <tr key={job.id}>
          <td><StatusText status={job.status} /></td><td><code>{assetId || "—"}</code></td>
          <td>{providerLabel(job.provider)}</td><td>{usage?.model || "—"}</td><td>{modeLabel(mode)}</td>
          <td>{usage?.metadata_profile || "—"}</td><td>{job.attempt_count}/{job.max_attempts}</td>
          <td>{formatDuration(job.claimed_at || job.created_at, job.completed_at || (job.status === "processing" ? job.updated_at : null))}</td>
          <td>{formatCost(usage?.estimated_cost_micros, usage?.currency)}</td><td><code>{job.error?.code || "—"}</code></td>
          <td><div className="ops-job-actions">
            {assetId ? <a aria-label={`View asset ${assetId}`} href={`/?details=1&asset=${encodeURIComponent(assetId)}`}>View</a> : <span title="Asset identity is not available yet">Unavailable</span>}
            <ProcessingJobAction job={job} permissions={permissions} onAccepted={onActionAccepted} />
          </div></td>
        </tr>;
      })}</tbody>
    </table></div>
    <div className="ops-pagination" aria-label="Processing pagination"><button type="button" disabled={filters.page <= 1} onClick={() => onFilters(pageFilters(filters, filters.page - 1))}>Previous</button><span>Page {filters.page} of {pages}</span><button type="button" disabled={filters.page >= pages} onClick={() => onFilters(pageFilters(filters, filters.page + 1))}>Next</button></div>
  </div>;
}

export type ProcessingJobActionKind = "retry" | "cancel";

export function eligibleProcessingAction(job: AiOpsJob): ProcessingJobActionKind | null {
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
  const requiredPermission = action === "retry" ? "ai_jobs.retry" : "ai_jobs.cancel";
  if (action && !permissions.includes(requiredPermission)) return null;
  if (!action) return null;
  const running = job.status === "processing";
  const label = action === "retry" ? "Retry failed job" : running ? "Request cancellation" : "Cancel queued job";

  async function submit() {
    if (!reason.trim()) return;
    setBusy(true); setMessage("");
    try {
      const result = action === "retry"
        ? await retryAiOperationsJob(job.id, reason.trim())
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

function CostUsage({ data, filters }: { data: AiOpsDashboardData; filters: AiOpsFilters }) {
  if (!data.usage.items.length) return <DashboardState kind="empty" label="No usage records in this period" />;
  return <div className="ops-content">
    <div className="ops-section-heading"><div><h2>Cost & Usage</h2><p>Estimated, provider-reported and reconciled values remain explicitly separate.</p></div><a href={aiOperationsExportUrl("usage", filters)}>Export usage CSV</a></div>
    {data.summary && <section className="ops-cost-summary" aria-label="Cost totals for selected period">
      <article><span>Estimated total</span><strong>{formatCost(data.summary.cost.estimated_cost_micros, data.summary.cost.currency)}</strong></article>
      <article><span>Provider-reported total</span><strong>{formatCost(data.summary.cost.provider_reported_cost_micros, data.summary.cost.currency)}</strong></article>
      <article><span>Reconciled total</span><strong>{formatCost(data.summary.cost.reconciled_cost_micros, data.summary.cost.currency)}</strong></article>
    </section>}
    <div className="ops-table-scroll"><table className="ops-data-table"><caption>AI cost and usage records</caption><thead><tr>{["Date", "Provider", "Model", "Mode", "Input units", "Output units", "Estimated cost", "Provider-reported cost"].map(value => <th key={value}>{value}</th>)}</tr></thead><tbody>{data.usage.items.map(item => <tr key={item.id}>
      <td><time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString()}</time></td><td>{providerLabel(item.provider)}</td><td>{item.model || "—"}</td><td>{modeLabel(item.processing_mode)}</td><td>{item.input_units.toLocaleString()}</td><td>{item.output_units.toLocaleString()}</td><td>{formatCost(item.estimated_cost_micros, item.currency)}</td><td>{formatCost(item.provider_reported_cost_micros, item.currency)}</td>
    </tr>)}</tbody></table></div>
  </div>;
}

export function StatusText({ status }: { status: string }) {
  return <span className={`ops-status ${status}`} aria-label={`Status: ${status.replaceAll("_", " ")}`}><i aria-hidden="true" />{status.replaceAll("_", " ")}</span>;
}

export function DashboardSkeleton() {
  return <div className="ops-skeleton" aria-busy="true" aria-label="Loading AI Operations dashboard"><i /><i /><i /><i /><span>Loading AI Operations…</span></div>;
}

export function DashboardState({ kind, label, onRetry }: { kind: "empty" | "unauthorized"; label?: string; onRetry?: () => void }) {
  return <div className={`ops-state ${kind}`} role={kind === "unauthorized" ? "alert" : "status"}><strong>{kind === "unauthorized" ? "AI Operations access required" : label || "No AI activity in this period"}</strong><p>{kind === "unauthorized" ? (label || "Sign in with an authorized account that has ai_operations.read.") : "Try a wider date range or clear one of the filters."}</p>{onRetry && <button type="button" onClick={onRetry}>Retry</button>}</div>;
}
