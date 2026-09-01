import { useEffect, useState } from "react";

import { fetchAiOperationsDashboard, type AiOpsJob } from "../../features/ai_operations";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";

const filters = {
  range: 30 as const,
  provider: "",
  model: "",
  processingMode: "",
  metadataProfile: "",
  status: "",
  page: 1,
  pageSize: 25 as const,
};

export function JobQueuePage() {
  const [jobs, setJobs] = useState<AiOpsJob[]>([]);
  const [summary, setSummary] = useState({ queued: 0, running: 0, completed: 0, failed: 0 });
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    fetchAiOperationsDashboard(filters)
      .then(response => {
        setJobs(response.data.jobs.items);
        if (response.data.summary) {
          setSummary({
            queued: response.data.summary.queued ?? 0,
            running: response.data.summary.running ?? 0,
            completed: response.data.summary.completed ?? 0,
            failed: response.data.summary.failed ?? 0,
          });
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return <main className="ops-shell">
    <aside className="ops-sidebar">
      <div className="brand">
        <b><BrandIcon /></b>
        <span><strong>Creative assets</strong><small>Operations console</small></span>
      </div>
      <WorkspaceNavigation active="queue" />
      <small className="ops-sidebar-note">Track tenant-scoped processing work across every job status.</small>
    </aside>
    <section className="ops-main">
      <div className="job-queue-page">
        <header>
          <div><small>OPERATIONS</small><h1>Job Queue</h1><p>Track file processing work across all statuses.</p></div>
          <button onClick={load}>Refresh</button>
        </header>
        <section className="job-queue-kpis">
          {Object.entries(summary).map(([key, value]) => <article key={key}><small>{key}</small><strong>{Number(value || 0).toLocaleString()}</strong></article>)}
        </section>
        <section className="job-queue-table">
          <h2>Recent jobs</h2>
          {loading ? <p>Loading jobs...</p> : <table>
            <thead><tr><th>Job</th><th>Provider</th><th>Status</th><th>Attempts</th></tr></thead>
            <tbody>{jobs.map(job => <tr key={job.id}>
              <td><b>{job.filename || job.job_type}</b><small>{job.job_type}</small></td>
              <td>{job.provider || "-"}</td>
              <td><span className={`job-status ${job.status}`}>{job.status}</span></td>
              <td>{job.attempt_count}/{job.max_attempts}</td>
            </tr>)}</tbody>
          </table>}
        </section>
      </div>
    </section>
  </main>;
}
