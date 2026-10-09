import { useCallback, useEffect, useState } from "react";
import { getSkillJobLog } from "./api";
import type { SkillJobLog } from "./api";

type JobStatusTone = "success" | "warning" | "danger" | "info" | "neutral";
type LogIconName = "terminal" | "refresh" | "close" | "check" | "alert" | "clock" | "timer" | "hourglass" | "activity" | "file" | "list" | "info";

function LogIcon({ name }: { name: LogIconName }) {
  const paths: Record<LogIconName, React.ReactNode> = {
    terminal: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3m6 0h4" /></>,
    refresh: <><path d="M20 11a8 8 0 1 0-2.4 6" /><path d="M20 4v7h-7" /></>,
    close: <path d="M5 5 19 19M19 5 5 19" />,
    check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.6 2.6L16 9" /></>,
    alert: <><path d="M10 4 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L14 4a2.3 2.3 0 0 0-4 0Z" /><path d="M12 9v4m0 3h.01" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    timer: <><circle cx="12" cy="13" r="8" /><path d="M12 9v5l3 2M9 2h6M12 5V2" /></>,
    hourglass: <><path d="M6 3h12M6 21h12M7 3c0 5 5 5 5 9s-5 4-5 9m10-18c0 5-5 5-5 9s5 4 5 9" /></>,
    activity: <path d="M2 12h4l3-7 5 14 3-7h5" />,
    file: <><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z" /><path d="M13 2v7h7M8 14h8m-8 4h6" /></>,
    list: <><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5m0-8h.01" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}

function statusPresentation(status: string, hasError = false): { tone: JobStatusTone; label: string; icon: LogIconName } {
  if (status === "completed" || status === "succeeded") return { tone: "success", label: "Completed", icon: "check" };
  if (status === "failed" || status === "error" || status === "cli_failed") return { tone: "danger", label: "Failed", icon: "alert" };
  if (status === "running" || status === "processing") return { tone: "info", label: "Running", icon: "activity" };
  if (status === "queued" || status === "pending") return { tone: "warning", label: hasError ? "Retry pending" : "Queued", icon: "hourglass" };
  if (status === "cancelled" || status === "canceled") return { tone: "neutral", label: "Cancelled", icon: "info" };
  return { tone: "neutral", label: status.replaceAll("_", " "), icon: "info" };
}

function displayDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function displayTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString();
}

function displayDuration(ms: number) {
  const seconds = Math.max(0, Math.floor((ms || 0) / 1000));
  return seconds >= 60 ? Math.floor(seconds / 60) + "m " + seconds % 60 + "s" : seconds + "s";
}

export function SkillJobLogDialog({
  stage, jobId, onClose,
}: { stage: "stage1" | "stage2" | "stage4"; jobId: string; onClose: () => void }) {
  const [data, setData] = useState<SkillJobLog | null>(null);
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async (signal?: AbortSignal, initial = false) => {
    if (initial) setLoading(true);
    else setRefreshing(true);
    try {
      const value = await getSkillJobLog(stage, jobId, signal);
      if (signal?.aborted) return;
      setData(value);
      setError("");
      setRefreshError("");
    } catch (reason) {
      if (signal?.aborted) return;
      const message = reason instanceof Error ? reason.message : "Could not load job log.";
      if (initial) setError(message);
      else setRefreshError(message);
    } finally {
      if (!signal?.aborted) {
        if (initial) setLoading(false);
        else setRefreshing(false);
      }
    }
  }, [stage, jobId]);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    setRefreshError("");
    void refresh(controller.signal, true);
    return () => controller.abort();
  }, [refresh]);
  // Auto-poll only while the job may change. Keep historical attempts visible.
  useEffect(() => {
    if (data?.status !== "running" && data?.status !== "queued") return;
    const controller = new AbortController();
    const interval = window.setInterval(() => {
      void getSkillJobLog(stage, jobId, controller.signal)
        .then(value => { if (!controller.signal.aborted) { setData(value); setRefreshError(""); } })
        .catch(() => undefined);
    }, 5000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [stage, jobId, data?.status]);

  useEffect(() => {
    function keydown(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [onClose]);

  const summary = statusPresentation(data?.status || "queued");
  const completedCount = data?.attempts.filter(attempt => attempt.status === "completed").length || 0;
  const errorCount = data?.attempts.filter(attempt => Boolean(attempt.error_code) || attempt.status === "failed").length || 0;

  return <div className="rrugc-skill-modal-backdrop" role="presentation"
    onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className="rrugc-job-log-dialog rrugc-skill-log-dialog" role="dialog" aria-modal="true"
      aria-labelledby="rrugc-job-log-title">
      <header className="rrugc-log-header">
        <div className="rrugc-log-heading">
          <span className="rrugc-log-heading-icon"><LogIcon name="terminal" /></span>
          <div className="rrugc-log-heading-text">
            <small>{stage.toUpperCase()} · SKILL EXECUTION</small>
            <h2 id="rrugc-job-log-title">Job attempts and logs</h2>
            <p title={jobId}><strong>{data?.skill.name || "Loading Skill…"}</strong><span>· {data?.skill.version ? "v" + data.skill.version : "current"}</span>
              <span className="rrugc-log-job-id">· {jobId}</span></p>
          </div>
        </div>
        <div className="rrugc-log-header-actions">
          <button type="button" className="rrugc-log-icon-button" aria-label="Refresh job logs"
            title="Refresh logs" disabled={loading || refreshing} onClick={() => void refresh()}>
            <LogIcon name="refresh" />
          </button>
          <button type="button" className="rrugc-log-icon-button" aria-label="Close job logs" title="Close"
            onClick={onClose}><LogIcon name="close" /></button>
        </div>
      </header>

      {loading && <div className="rrugc-log-state" role="status"><LogIcon name="hourglass" />Loading execution history…</div>}
      {error && <p role="alert" className="rrugc-log-fetch-error"><LogIcon name="alert" />{error}</p>}
      {refreshError && <p role="alert" className="rrugc-log-fetch-error"><LogIcon name="alert" />Refresh failed: {refreshError}</p>}
      {!loading && data && <div className="rrugc-log-body">
        <div className="rrugc-log-summary" aria-label="Job summary">
          <span className={"rrugc-log-status is-" + summary.tone} role="status"><LogIcon name={summary.icon} />{summary.label}</span>
          <span className="rrugc-log-summary-stat"><LogIcon name="list" />{data.attempts.length} {data.attempts.length === 1 ? "attempt" : "attempts"}</span>
          {completedCount > 0 && <span className="rrugc-log-summary-stat"><LogIcon name="check" />{completedCount} completed</span>}
          {errorCount > 0 && <span className="rrugc-log-summary-stat is-error"><LogIcon name="alert" />{errorCount} with errors</span>}
          {(data.status === "queued" || data.status === "running") && <span className="rrugc-log-auto-update"><span /> Auto-refresh every 5s</span>}
        </div>
        <div className="rrugc-job-log-list" aria-label="Execution attempts">
          {data.attempts.map((attempt, index) => {
            const retryPending = (attempt.status === "queued" || attempt.status === "pending") && Boolean(attempt.error_code);
            const state = statusPresentation(attempt.status, Boolean(attempt.error_code));
            return <article className={"rrugc-log-attempt is-" + state.tone} key={attempt.id}>
              <div className="rrugc-log-attempt-marker" aria-hidden="true"><LogIcon name={state.icon} /></div>
              <div className="rrugc-log-attempt-content">
                <div className="rrugc-log-attempt-heading">
                  <div className="rrugc-log-attempt-title">
                    <strong>Run {data.attempts.length - index}</strong>
                    <span className={"rrugc-log-status is-" + state.tone}><LogIcon name={state.icon} />{state.label}</span>
                  </div>
                  <time dateTime={attempt.created_at} title={displayDate(attempt.created_at)}>
                    <LogIcon name="clock" />{displayDate(attempt.created_at)}
                  </time>
                </div>
                <div className="rrugc-log-attempt-meta">
                  <span><LogIcon name="activity" />Worker attempt {attempt.attempt_count}/{attempt.max_attempts}</span>
                  <span><LogIcon name="timer" />{displayDuration(attempt.duration_ms)}</span>
                </div>
                {attempt.error_code && <div className="rrugc-log-error-detail" role="note">
                  <LogIcon name="alert" />
                  <div><strong>{attempt.error_message || "This attempt encountered an error."}</strong>
                    <code>{attempt.error_code}</code>
                    {retryPending && <small>The previous execution stopped; this job is waiting to retry.</small>}
                  </div>
                </div>}
                {!attempt.error_code && attempt.error_message && <p className="rrugc-log-attempt-message">{attempt.error_message}</p>}
                {attempt.execution && <div className="rrugc-job-live-progress">
                  <div className="rrugc-log-execution-header"><span className="rrugc-log-execution-icon"><LogIcon name="terminal" /></span>
                    <div><strong>Codex · {attempt.execution.state.replaceAll("_", " ")} · {Math.floor((attempt.execution.elapsed_seconds || 0) / 60)}m {(attempt.execution.elapsed_seconds || 0) % 60}s</strong>
                      <small>{attempt.execution.event_count} events · {Math.round(attempt.execution.stdout_bytes / 1024)} KB streamed · Last: {attempt.execution.last_event}</small>
                    </div>
                  </div>
                  {attempt.execution.events.length > 0 && <details>
                    <summary><LogIcon name="file" /> Recent execution events <span>{attempt.execution.events.length}</span></summary>
                    <ol>{attempt.execution.events.map((entry, eventIndex) =>
                      <li key={eventIndex}><time dateTime={entry.time}>{displayTime(entry.time)}</time><span>{entry.event}{entry.item ? " · " + entry.item : ""}</span></li>
                    )}</ol>
                  </details>}
                </div>}
              </div>
            </article>;
          })}
          {!data.attempts.length && <div className="rrugc-log-empty"><LogIcon name="info" />No processing history recorded yet.</div>}
        </div>
      </div>}
    </section>
  </div>;
}
