import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchAiOperationsJobQueue, type AiOpsJob } from "../../features/ai_operations";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import geminiSparkle from "../../assets/gemini-sparkle.svg";

type QueueStatus = "" | "queued" | "running" | "completed" | "failed";
type PageSize = 25 | 50 | 100;

const statusTabs: Array<{ id: QueueStatus; label: string }> = [
  { id: "", label: "All generations" },
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

export function formatJobDuration(job: Pick<AiOpsJob, "status" | "claimed_at" | "processing_duration_ms">, now = Date.now()) {
  let durationMs = Number(job.processing_duration_ms || 0);
  if (job.status === "processing" && job.claimed_at) {
    const claimedAt = new Date(job.claimed_at).valueOf();
    if (Number.isFinite(claimedAt)) durationMs += Math.max(0, now - claimedAt);
  }
  if (!Number.isFinite(durationMs) || durationMs <= 0) return "—";
  if (durationMs < 1_000) return `${Math.round(durationMs)} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(1)} s`;
  const totalSeconds = Math.round(durationMs / 1_000);
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  return hours ? `${hours} h ${minutes} min ${seconds} s` : `${minutes} min ${seconds} s`;
}

export function jobStatusLabel(job: Pick<AiOpsJob, "status" | "is_deferred"> & { waiting_reason?: string | null }) {
  if (job.waiting_reason === "gemini_image_quota_deferred") return "Waiting for Gemini quota";
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

export function JobProviderIcon({ provider }: { provider: string | null }) {
  if (provider === "cloudflare_sd") {
    return <span className="job-provider-icon cloudflare" aria-hidden="true">
      <svg viewBox="0 0 64 32"><path fill="#f48120" d="M18 26h28.5c5.2 0 9.5-3.8 10.3-8.8.2-.8.2-1.6.2-2.4 0-6.3-5.1-11.4-11.4-11.4-1.2 0-2.3.2-3.4.5C40.1 1.5 36.7 0 33 0c-5.8 0-10.7 3.7-12.5 8.9-.8-.3-1.7-.5-2.6-.5-4.3 0-7.9 3.3-8.2 7.6C4.2 16.5 0 20.8 0 26h18Z"/><path fill="#faad3d" d="M18 26h31.6c3.4 0 6.2-2.8 6.2-6.2 0-.4 0-.8-.1-1.2-1.1 4.3-5 7.4-9.6 7.4H18Z"/></svg>
    </span>;
  }
  if (provider === "gemini") return <span className="job-provider-icon gemini" aria-hidden="true"><img src={geminiSparkle} alt="" /></span>;
  if (provider === "adobe_firefly") return <span className="job-provider-icon firefly" aria-hidden="true"><img src="/brands/adobe-firefly.svg" alt="" /></span>;
  return <span className="job-provider-icon generic" aria-hidden="true">AI</span>;
}

export function GenerationResultModal({ job, onClose }: { job: AiOpsJob; onClose: () => void }) {
  if (!job.source_thumbnail_url || !job.generated_image_url) return null;

  return <div
    className="generation-result-backdrop"
    role="presentation"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}
  >
    <section className="generation-result-modal" role="dialog" aria-modal="true" aria-labelledby="generation-result-title">
      <header>
        <div>
          <small>GENERATE SQUARE 1:1</small>
          <h2 id="generation-result-title">Generation result</h2>
          <p title={job.filename || undefined}>{job.filename || "Generated image"}</p>
        </div>
        <button type="button" className="generation-result-close-icon" onClick={onClose} aria-label="Close result comparison">&times;</button>
      </header>
      <div className="generation-result-comparison">
        <figure>
          <figcaption><strong>Original</strong><span>Source image</span></figcaption>
          <div><img src={job.source_thumbnail_url} alt="Original source" /></div>
        </figure>
        <figure>
          <figcaption><strong>Generated</strong><span>{job.provider ? formatJobType(job.provider) : "Generated result"}</span></figcaption>
          <div><img src={job.generated_image_url} alt="Generated result" /></div>
        </figure>
      </div>
      <footer>
        <a href={job.generated_image_url} target="_blank" rel="noreferrer">Open full size</a>
        <button type="button" onClick={onClose}>Close</button>
      </footer>
    </section>
  </div>;
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
  const [selectedResult, setSelectedResult] = useState<AiOpsJob | null>(null);
  const [clockNow, setClockNow] = useState(() => Date.now());

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
      jobType: "image_generate",
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

  useEffect(() => {
    if (!jobs.some(job => job.status === "processing")) return;
    setClockNow(Date.now());
    const timer = window.setInterval(() => setClockNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [jobs]);

  useEffect(() => {
    if (!selectedResult) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setSelectedResult(null); };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [selectedResult]);

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
      <small className="ops-sidebar-note">Track square image generation across every job status.</small>
    </aside>

    <section className="ops-main job-queue-main">
      <header className="job-queue-header">
        <div>
          <small>OPERATIONS</small>
          <h1>Job Queue</h1>
          <p>Monitor Generate Square 1:1 jobs, retries, failures, and completed images.</p>
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
                <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Source image or job ID" />
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
            <div><h2>Square generation jobs</h2><p>{status ? `Showing ${formatJobType(status).toLowerCase()} generations` : "All recent Generate Square 1:1 jobs"}</p></div>
            <strong>{total.toLocaleString()} total</strong>
          </header>
          <div className="job-queue-table-scroll">
            <table className="job-queue-table">
              <thead><tr><th>Job</th><th>Source</th><th>Generated</th><th>Provider</th><th>AI model</th><th>Status</th><th>Attempts</th><th>Duration</th><th>Updated</th><th>Action</th></tr></thead>
              <tbody>
                {loading && !jobs.length ? <tr><td colSpan={10} className="job-queue-empty">Loading jobs...</td></tr> : visibleJobs.map(job => <tr key={job.id}>
                  <td>
                    <div className="job-queue-job">
                      <svg className="job-queue-file-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></svg>
                      <span><b>{job.filename || formatJobType(job.job_type)}</b><small>{formatJobType(job.job_type)} / {job.id}</small></span>
                    </div>
                  </td>
                  <td><div className="job-image-preview">{job.source_thumbnail_url ? <img src={job.source_thumbnail_url} alt="Source image" loading="lazy" /> : <span>No preview</span>}</div></td>
                  <td><div className={"job-image-preview generated" + (job.generated_image_url ? " available" : "")}>{job.generated_image_url ? <img src={job.generated_image_url} alt="Generated image" loading="lazy" /> : <span>{job.is_deferred ? "Waiting for quota" : job.status === "failed" ? "Unavailable" : "Generating"}</span>}</div></td>
                  <td><span className="job-provider"><JobProviderIcon provider={job.provider} /><span>{job.provider ? formatJobType(job.provider) : "Local"}</span></span></td>
                  <td><span className="job-ai-model">{job.ai_model || "Default"}</span></td>
                  <td><span className={`job-status ${job.is_deferred ? "waiting" : job.status}`}><i />{jobStatusLabel(job)}</span>{job.waiting_reason && <small className="job-waiting-reason">{job.waiting_reason === "gemini_image_quota_deferred" ? ("Next attempt " + formatJobTime(job.next_attempt_at)) : formatJobType(job.waiting_reason)}</small>}</td>
                  <td><b>{job.attempt_count}</b><span className="job-attempt-limit"> / {job.max_attempts}</span></td>
                  <td><span className="job-duration">{formatJobDuration(job, clockNow)}</span></td>
                  <td><time dateTime={job.updated_at}>{formatJobTime(job.updated_at)}</time></td>
                  <td>{job.status === "completed" && job.source_thumbnail_url && job.generated_image_url
                    ? <button type="button" className="job-view-result" onClick={() => setSelectedResult(job)}>View result</button>
                    : <span className="job-result-unavailable">&mdash;</span>}</td>
                </tr>)}
                {!loading && !visibleJobs.length && <tr><td colSpan={10} className="job-queue-empty">No jobs match the selected filters.</td></tr>}
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
    {selectedResult && <GenerationResultModal job={selectedResult} onClose={() => setSelectedResult(null)} />}
  </main>;
}
