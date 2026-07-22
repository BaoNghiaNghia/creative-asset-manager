import { useEffect, useMemo, useState } from "react";
import {
  aiOperationsExportUrl, fetchAiOperationsDashboard, filtersFromSearch, searchFromFilters,
  type AiOpsDashboardData, type AiOpsFilters, type AiOpsJob, type AiOpsUsage,
} from "../../features/ai_operations";
import { AccessibleChart } from "./AccessibleChart";
import { ConfigurationTab, ProvidersTab } from "./ProvidersConfiguration";
import {
  dailyProviderCostChart, dailyStatusChart, failureChart,
  formatCost, formatDuration, modeLabel, providerLabel, providerVolumeChart,
} from "./presentation";

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
  const [data, setData] = useState<AiOpsDashboardData>(() => emptyDashboard(filters.page));
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<string[]>([]);
  const [unauthorized, setUnauthorized] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetchAiOperationsDashboard(filters, (url, init) => fetch(url, { ...init, signal: controller.signal }))
      .then(result => {
        setData(result.data); setErrors(result.errors); setUnauthorized(result.unauthorized);
      })
      .catch(error => setErrors([String(error?.message || "Dashboard request failed")]))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [filters, reload]);

  function changeFilters(next: AiOpsFilters) {
    setFilters(next);
    updateUrl(next, tab);
  }
  function changeTab(next: AiOpsTab) {
    setTab(next);
    updateUrl(filters, next);
  }
  return <AiOperationsShell>
    <AiOperationsContent
      data={data} loading={loading} errors={errors} unauthorized={unauthorized}
      filters={filters} tab={tab} onTab={changeTab} onFilters={changeFilters}
      onRetry={() => setReload(value => value + 1)}
    />
  </AiOperationsShell>;
}

function updateUrl(filters: AiOpsFilters, tab: AiOpsTab) {
  const query = searchFromFilters(filters, tab);
  window.history.replaceState({}, "", `/ai-operations${query ? `?${query}` : ""}`);
}

export function AiOperationsShell({ children }: { children: React.ReactNode }) {
  return <main className="ops-shell">
    <aside className="ops-sidebar">
      <div className="brand"><b>C</b><span><strong>Creative assets</strong><small>Operations console</small></span></div>
      <p>WORKSPACE</p>
      <a href="/">▧ Asset Explorer</a>
      <a href="/ai-operations" className="active" aria-current="page">◉ AI Operations</a>
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
};

export function AiOperationsContent({
  data, filters, tab, loading = false, errors = [], unauthorized = false,
  onTab, onFilters, onRetry,
}: ContentProps) {
  const models = useMemo(() => [...new Set([
    ...data.providers.map(item => item.model || ""), ...data.usage.items.map(item => item.model || ""),
  ].filter(Boolean))].sort(), [data]);
  const profiles = useMemo(() => [...new Set(data.usage.items.map(item => item.metadata_profile || "").filter(Boolean))].sort(), [data]);
  if (unauthorized) return <DashboardState kind="unauthorized" onRetry={onRetry} />;
  return <>
    <header className="ops-header">
      <div><small>OPERATIONS</small><h1>AI Operations</h1><p>Processing health, usage and cost for the current tenant.</p></div>
      <a href="/">Back to assets</a>
    </header>
    <nav className="ops-tabs" aria-label="AI Operations sections">
      {tabs.map(item => <button key={item.id} type="button" className={tab === item.id ? "active" : ""} aria-current={tab === item.id ? "page" : undefined} onClick={() => onTab(item.id)}>{item.label}</button>)}
    </nav>
    <AiOperationsFilters filters={filters} models={models} profiles={profiles} onChange={onFilters} />
    <nav className="ops-export-actions" aria-label="AI Operations CSV exports">{(["daily", "usage", "failures", "jobs"] as const).map(kind => <a key={kind} href={aiOperationsExportUrl(kind, filters)}>Export {kind} CSV</a>)}</nav>
    {errors.length > 0 && <div className="ops-partial-error" role="alert">
      <div><b>Some dashboard data could not be loaded.</b><span>{errors.join(" · ")}</span></div>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>}
    {loading ? <DashboardSkeleton /> : tab === "overview" ? <Overview data={data} />
      : tab === "processing" ? <Processing data={data} filters={filters} onFilters={onFilters} />
      : tab === "cost" ? <CostUsage data={data} filters={filters} />
      : tab === "providers" ? <ProvidersTab metrics={data.todayProviders} />
      : <ConfigurationTab />}
  </>;
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
    ["Failed", summary?.failed || 0], ["Running", summary?.running || 0],
    ["Queued", summary?.queued || 0], ["Success rate", `${((summary?.success_rate || 0) * 100).toFixed(1)}%`],
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

function Processing({ data, filters, onFilters }: { data: AiOpsDashboardData; filters: AiOpsFilters; onFilters: (value: AiOpsFilters) => void }) {
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
          <td>{assetId ? <a href={`/?details=1&asset=${encodeURIComponent(assetId)}`}>View</a> : <span title="Asset identity is not available yet">Unavailable</span>}</td>
        </tr>;
      })}</tbody>
    </table></div>
    <div className="ops-pagination" aria-label="Processing pagination"><button type="button" disabled={filters.page <= 1} onClick={() => onFilters({ ...filters, page: filters.page - 1 })}>Previous</button><span>Page {filters.page} of {pages}</span><button type="button" disabled={filters.page >= pages} onClick={() => onFilters({ ...filters, page: filters.page + 1 })}>Next</button></div>
  </div>;
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
  return <span className={`ops-status ${status}`}><i aria-hidden="true" />{status.replaceAll("_", " ")}</span>;
}

export function DashboardSkeleton() {
  return <div className="ops-skeleton" aria-busy="true" aria-label="Loading AI Operations dashboard"><i /><i /><i /><i /><span>Loading AI Operations…</span></div>;
}

export function DashboardState({ kind, label, onRetry }: { kind: "empty" | "unauthorized"; label?: string; onRetry?: () => void }) {
  return <div className={`ops-state ${kind}`} role={kind === "unauthorized" ? "alert" : "status"}><strong>{kind === "unauthorized" ? "AI Operations access required" : label || "No AI activity in this period"}</strong><p>{kind === "unauthorized" ? "Sign in with an authorized tenant operator or administrator account." : "Try a wider date range or clear one of the filters."}</p>{onRetry && <button type="button" onClick={onRetry}>Retry</button>}</div>;
}
