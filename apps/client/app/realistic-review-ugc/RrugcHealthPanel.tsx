import { useEffect, useState } from "react";
import { getRrugcHealth } from "./api";
import type { RrugcHealth } from "./types";
import { WorkflowStatusIcon } from "./WorkflowStatusIcon";

function ageLabel(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return seconds + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  return (seconds / 3600).toFixed(seconds < 7200 ? 1 : 0) + "h";
}

export function RrugcHealthPanel({ onError }: { onError: (message: string) => void }) {
  const [health, setHealth] = useState<RrugcHealth | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function refresh(signal?: AbortSignal) {
      try {
        setHealth(await getRrugcHealth(signal));
      } catch (reason) {
        if (!signal?.aborted) {
          onError(reason instanceof Error ? reason.message : "Unable to load RRUGC health.");
        }
      }
    }
    void refresh(controller.signal);
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 10_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [onError]);

  if (!health) {
    return <section className="rrugc-card rrugc-health-panel" aria-label="RRUGC health">
      <div className="rrugc-section-heading">
        <div><small>SYSTEM HEALTH</small><h2><WorkflowStatusIcon name="heartbeat" size={19} />Pipeline health</h2></div>
      </div>
      <div className="rrugc-health-grid is-loading" aria-busy="true" />
    </section>;
  }

  const queueWarning = health.orphan_analysis_queued > 0 || health.stale_importing > 0
    || (health.oldest_analysis_queue_age_seconds !== null && health.oldest_analysis_queue_age_seconds > 3600);
  const geminiWarning = health.gemini_deferred > 0 || !health.gemini_capacity_available;
  const scoutWarning = health.scout_offline > 0 || health.scout_outdated > 0;

  return <section className="rrugc-card rrugc-health-panel" aria-label="RRUGC health">
    <div className="rrugc-section-heading rrugc-health-heading">
      <div>
        <small>SYSTEM HEALTH</small>
        <h2><WorkflowStatusIcon name="heartbeat" size={19} />Pipeline health</h2>
        <p>Queue, Gemini capacity and Scout connections · live monitoring</p>
      </div>
      <span className={"rrugc-auto-scout-health " + (queueWarning || geminiWarning || scoutWarning ? "is-warning" : "is-online")}>
        <WorkflowStatusIcon name={queueWarning || geminiWarning || scoutWarning ? "alert" : "shield-check"} size={14} />
        {queueWarning || geminiWarning || scoutWarning ? "Needs attention" : "Healthy"}
      </span>
    </div>

    <div className="rrugc-health-grid">
      <article className={health.orphan_analysis_queued ? "is-warning" : ""}>
        <small><WorkflowStatusIcon name="list" size={13} />Orphan analysis</small>
        <strong>{health.orphan_analysis_queued}</strong>
        <span>Queued without active job</span>
      </article>
      <article className={health.stale_importing ? "is-warning" : ""}>
        <small><WorkflowStatusIcon name="download" size={13} />Stuck importing</small>
        <strong>{health.stale_importing}</strong>
        <span>Past watchdog threshold</span>
      </article>
      <article className={health.gemini_deferred ? "is-warning" : ""}>
        <small><WorkflowStatusIcon name="sparkles" size={13} />Gemini deferred</small>
        <strong>{health.gemini_deferred}</strong>
        <span>{health.gemini_capacity_available ? "Capacity available" : "Waiting for capacity"}</span>
      </article>
      <article className={health.oldest_analysis_queue_age_seconds !== null && health.oldest_analysis_queue_age_seconds > 3600 ? "is-warning" : ""}>
        <small><WorkflowStatusIcon name="clock" size={13} />Oldest AI queue</small>
        <strong>{ageLabel(health.oldest_analysis_queue_age_seconds)}</strong>
        <span>Pending / retry age</span>
      </article>
      <article className={scoutWarning ? "is-warning" : ""}>
        <small><WorkflowStatusIcon name="monitor" size={13} />Scout machines</small>
        <strong>{health.scout_online}/{health.scout_total}</strong>
        <span>{health.scout_outdated ? health.scout_outdated + " outdated" : health.scout_offline ? health.scout_offline + " offline" : "Current"}</span>
      </article>
    </div>
  </section>;
}
