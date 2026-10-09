import { useEffect, useState } from "react";
import { getSkillJobLog } from "./api";
import type { SkillJobLog } from "./api";

export function SkillJobLogDialog({
  stage, jobId, onClose,
}: { stage: "stage1" | "stage2" | "stage4"; jobId: string; onClose: () => void }) {
  const [data, setData] = useState<SkillJobLog | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void getSkillJobLog(stage, jobId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setData(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load job log."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [stage, jobId]);
  // Long-running Codex generations emit progress for tens of minutes.
  // Refresh lightweight summaries while the job is active, not just on open.
  useEffect(() => {
    if (data?.status !== "running" && data?.status !== "queued") return;
    const controller = new AbortController();
    const interval = window.setInterval(() => {
      void getSkillJobLog(stage, jobId, controller.signal)
        .then(value => { if (!controller.signal.aborted) setData(value); })
        .catch(() => undefined);
    }, 5000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [stage, jobId, data?.status]);
  useEffect(() => {
    function keydown(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [onClose]);
  return <div className="rrugc-skill-modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className="rrugc-job-log-dialog" role="dialog" aria-modal="true" aria-labelledby="rrugc-job-log-title">
      <header><div><small>{stage.toUpperCase()} · SKILL EXECUTION</small><h2 id="rrugc-job-log-title">Job attempts and logs</h2>
        <p>{data?.skill.name || "Loading Skill…"} · {data?.skill.version ? "v" + data.skill.version : "current"} · {jobId}</p></div>
        <button type="button" aria-label="Close job logs" onClick={onClose}>×</button></header>
      {loading && <p role="status">Loading execution log…</p>}
      {error && <p role="alert" className="rrugc-source-error">{error}</p>}
      {!loading && !error && data && <div className="rrugc-job-log-list">
        <strong>Status: {data.status} · {data.attempts.length} execution {data.attempts.length === 1 ? "record" : "records"}</strong>
        {data.attempts.map((attempt, index) => <article key={attempt.id}>
          <div><b>Run {data.attempts.length - index} · {attempt.status}</b><span>{new Date(attempt.created_at).toLocaleString()}</span></div>
          <small>Worker attempt {attempt.attempt_count}/{attempt.max_attempts} · {Math.round((attempt.duration_ms || 0) / 1000)}s</small>
          {attempt.error_code && <code>{attempt.error_code}</code>}
          {attempt.error_message && <p>{attempt.error_message}</p>}
          {attempt.execution && <div className="rrugc-job-live-progress" aria-live="polite">
            <strong>Codex · {attempt.execution.state.replaceAll("_", " ")} · {Math.floor((attempt.execution.elapsed_seconds || 0) / 60)}m {(attempt.execution.elapsed_seconds || 0) % 60}s</strong>
            <small>{attempt.execution.event_count} events · {Math.round(attempt.execution.stdout_bytes / 1024)} KB streamed · Last: {attempt.execution.last_event}</small>
            <details>
              <summary>Recent execution events</summary>
              <ol>{attempt.execution.events.map((entry, eventIndex) =>
                <li key={eventIndex}><time>{new Date(entry.time).toLocaleTimeString()}</time> · {entry.event}{entry.item ? " / " + entry.item : ""}</li>
              )}</ol>
            </details>
          </div>}
        </article>)}
        {!data.attempts.length && <p>No processing history recorded yet.</p>}
      </div>}
    </section>
  </div>;
}
