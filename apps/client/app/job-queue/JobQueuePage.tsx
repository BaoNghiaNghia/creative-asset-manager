import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchAiOperationsJobQueue, type AiOpsJob } from "../../features/ai_operations";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";

type QueueStatus = "" | "queued" | "running" | "completed" | "failed";
type PageSize = 25 | 50 | 100;

const statusTabs: Array<{ id: QueueStatus; label: string }> = [
  { id: "", label: "All jobs" },
  { id: "queued", label: "Queued" },
  { id: "running", label: "Running" },
  { id: "completed", label: "Completed" },
  { id: "failed", label: "Failed" },
];

export function formatJobType(value: string) {
  return value.split("_").filter(Boolean).map(part => part[0]?.toUpperCase() + part.slice(1)).join(" ");
}

export function formatJobTime(value: string | null | undefined) {
  if (!value) return "Not started";
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Not available";
}

export function jobStatusLabel(job: Pick<AiOpsJob, "status" | "is_deferred">) {
  if (job.is_deferred) return "Waiting";
  const labels: Record<string, string> = {
    pending: "Queued",
    retry: "Retrying",
    processing: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return labels[job.status] || formatJobType(job.status);
}

export function JobQueuePage() {
  const [jobs, setJobs] = useState<AiOpsJob[]>([]);
  const [summary, setSummary] = useState({ queued: 0, running: 0, completed: 0, failed: 0 });
  const [status, setStatus] = useState<QueueStatus>("");
  const [provider, setProvider] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(25);
  const [total, setTotal] = useState(0);
  const [knownProviders, setKnownProviders] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    fetchAiOperationsJobQueue({
      range: 30,
      provider,
      model: "",
      processingMode: "",
      metadataProfile: "",
      status,
      page,
      pageSize,
    })
      .then(response => {
        const nextJobs = response.jobs.items;
        setJobs(nextJobs);
        setTotal(response.jobs.total);
        setKnownProviders(current => [...new Set([
          ...current,
          ...nextJobs.map(job => job.provider).filter((value): value is string => Boolean(value)),
        ])].sort());
        if (!status && !provider && response.summary) {
          setSummary({
            queued: response.summary.queued ?? 0,
            running: response.summary.running ?? 0,
            completed: response.summary.completed ?? 0,
            failed: response.summary.failed ?? 0,
          });
        }
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : "Job Queue could not be loaded."))
      .finally(() => setLoading(false));
  }, [page, pageSize, provider, status]);

  useEffect(() => { load(); }, [load]);

  const visibleJobs = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return jobs;
    return jobs.filter(job => [
      job.filename,
      job.job_type,
      job.provider,
      job.entity_id,
      job.error?.code,
    ].some(value => String(value || "").toLowerCase().includes(normalized)));
  }, [jobs, query]);

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const firstItem = total ? (page - 1) * pageSize + 1 : 0;
  const lastItem = Math.min(total, page * pageSize);

  const selectStatus = (next: QueueStatus) => {
    setStatus(next);
    setPage(1);
  };

  return <main className="ops-shell job-queue-shell">
    <aside className="ops-sidebar">
      <div className="brand">
        <b><BrandIcon /></b>
        <span><strong>Creative assets</strong><small>Operations console</small></span>
      </div>
      <WorkspaceNavigation active="queue" />
      <small className="ops-sidebar-note">Track tenant-scoped processing work across every job status.</small>
    </aside>

    <section className="ops-main job-queue-main">
      <header className="job-queue-header">
        <div>
          <small>OPERATIONS</small>
          <h1>Job Queue</h1>
          <p>Monitor file processing jobs, retries, failures, and completed work.</p>
        </div>
        <div className="job-queue-header-actions">
          <span>Workspace - Creative Assets</span>
          <a href="/">&larr; Back to assets</a>
        </div>
      </header>

      <nav className="job-queue-tabs" aria-label="Job Queue statuses">
        {statusTabs.map(tab => <button
          key={tab.id || "all"}
          type="button"
          className={status === tab.id ? "active" : undefined}
          aria-current={status === tab.id ? "page" : undefined}
          onClick={() => selectStatus(tab.id)}
        >{tab.label}</button>)}
      </nav>

      <div className="job-queue-content">
        <section className="job-queue-toolbar" aria-label="Job filters">
          <div>
            <small>FIND A JOB</small>
            <div className="job-queue-filter-fields">
              <label>Search
                <input value={query} onChange={event => setQuery(event.target.value)} placeholder="File, job type or ID" />
              </label>
              <label>Provider
                <select value={provider} onChange={event => { setProvider(event.target.value); setPage(1); }}>
                  <option value="">All providers</option>
                  {knownProviders.map(value => <option key={value} value={value}>{formatJobType(value)}</option>)}
                </select>
              </label>
              <label>Rows
                <select value={pageSize} onChange={event => { setPageSize(Number(event.target.value) as PageSize); setPage(1); }}>
                  <option value={25}>25 per page</option>
                  <option value={50}>50 per page</option>
                  <option value={100}>100 per page</option>
                </select>
              </label>
            </div>
          </div>
          <button type="button" onClick={load} disabled={loading}>{loading ? "Refreshing..." : "Refresh"}</button>
        </section>

        <section className="job-queue-kpis" aria-label="Job summary">
          {Object.entries(summary).map(([key, value]) => <article key={key} className={key}>
            <span><i />{formatJobType(key)}</span>
            <strong>{Number(value || 0).toLocaleString()}</strong>
          </article>)}
        </section>

        {error && <div className="job-queue-error" role="alert"><span>{error}</span><button type="button" onClick={load}>Try again</button></div>}

        <section className="job-queue-table-card">
          <header>
            <div><h2>Processing jobs</h2><p>{status ? `Showing ${formatJobType(status).toLowerCase()} jobs` : "All recent tenant processing jobs"}</p></div>
            <strong>{total.toLocaleString()} total</strong>
          </header>
          <div className="job-queue-table-scroll">
            <table className="job-queue-table">
              <thead><tr><th>Job</th><th>Provider</th><th>Status</th><th>Attempts</th><th>Updated</th></tr></thead>
              <tbody>
                {loading && !jobs.length ? <tr><td colSpan={5} className="job-queue-empty">Loading jobs...</td></tr> : visibleJobs.map(job => <tr key={job.id}>
                  <td>
                    <div className="job-queue-job">
                      <svg className="job-queue-file-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></svg>
                      <span><b>{job.filename || formatJobType(job.job_type)}</b><small>{formatJobType(job.job_type)} / {job.id}</small></span>
                    </div>
                  </td>
                  <td>{job.provider ? formatJobType(job.provider) : "Local"}</td>
                  <td><span className={`job-status ${job.is_deferred ? "waiting" : job.status}`}><i />{jobStatusLabel(job)}</span>{job.waiting_reason && <small className="job-waiting-reason">{formatJobType(job.waiting_reason)}</small>}</td>
                  <td><b>{job.attempt_count}</b><span className="job-attempt-limit"> / {job.max_attempts}</span></td>
                  <td><time dateTime={job.updated_at}>{formatJobTime(job.updated_at)}</time></td>
                </tr>)}
                {!loading && !visibleJobs.length && <tr><td colSpan={5} className="job-queue-empty">No jobs match the selected filters.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <footer className="job-queue-pagination">
          <button type="button" disabled={page <= 1 || loading} onClick={() => setPage(value => Math.max(1, value - 1))}>Previous</button>
          <span>{firstItem}-{lastItem} of {total.toLocaleString()} / Page {page} of {pageCount}</span>
          <button type="button" disabled={page >= pageCount || loading} onClick={() => setPage(value => Math.min(pageCount, value + 1))}>Next</button>
        </footer>
      </div>
    </section>
  </main>;
}
