import { useEffect, useState } from "react";
import {
  getDeliveryOperationsSummary,
  runDeliveryMaintenance,
} from "./api";
import type { DeliveryOperationsSummary } from "./types";

const relativeCadence = (seconds: number) => {
  if (seconds % 3600 === 0) return (seconds / 3600) + "h";
  if (seconds % 60 === 0) return (seconds / 60) + "m";
  return seconds + "s";
};

const eventTime = (value: string) => new Date(value).toLocaleString();

export function DeliveryOperationsView({
  summary,
  busy,
  onRun,
}: {
  summary: DeliveryOperationsSummary | null;
  busy: boolean;
  onRun: () => void;
}) {
  const automation = summary?.automation_enabled ?? false;
  return <section className="rrugc-card rrugc-operations-panel" aria-label="Delivery operations">
    <div className="rrugc-section-heading rrugc-operations-heading">
      <div>
        <small>DELIVERY OPERATIONS</small>
        <h2>Automation & retries</h2>
        <p>
          Scheduled maintenance retries partial deliveries, reconciles retention,
          and keeps the operational event feed current.
        </p>
      </div>
      <div className="rrugc-operations-actions">
        <span className={"rrugc-automation-badge " + (automation ? "is-on" : "is-off")}>
          {automation
            ? "Automation on · every " + relativeCadence(summary?.maintenance_interval_seconds ?? 0)
            : "Automation off"}
        </span>
        <button
          type="button"
          className="rrugc-primary"
          disabled={busy || !automation}
          onClick={onRun}
        >
          {busy ? "Queued…" : "Run maintenance now"}
        </button>
      </div>
    </div>

    <div className="rrugc-operations-stats">
      <span><small>Campaigns complete</small><b>{summary?.campaigns_completed ?? 0}/{summary?.campaigns_total ?? 0}</b></span>
      <span><small>Active destinations</small><b>{summary?.destinations_active ?? 0}</b></span>
      <span><small>Delivered packages</small><b>{summary?.packages_delivered ?? 0}</b></span>
      <span><small>Partial failed</small><b>{summary?.packages_partial_failed ?? 0}</b></span>
      <span><small>Retry due</small><b>{summary?.retry_due ?? 0}</b></span>
      <span><small>Retry exhausted</small><b>{summary?.retry_exhausted ?? 0}</b></span>
      <span><small>Items delivered</small><b>{summary?.items_delivered ?? 0}</b></span>
      <span><small>Items failed</small><b>{summary?.items_failed ?? 0}</b></span>
    </div>

    <div className="rrugc-operations-foot">
      <p>
        Auto retry budget: <strong>{summary?.auto_retry_max_attempts ?? 0}</strong>.
        Retention expiry changes package lifecycle state only; catalog originals remain untouched.
      </p>
      {summary?.latest_delivery_at && <p>
        Latest delivery: <strong>{eventTime(summary.latest_delivery_at)}</strong>
      </p>}
    </div>

    <div className="rrugc-operations-events">
      <div className="rrugc-operations-events-head">
        <strong>Recent delivery events</strong>
        <small>{summary?.recent_events.length ?? 0} shown</small>
      </div>
      {!summary?.recent_events.length
        ? <p className="rrugc-empty">No delivery events yet.</p>
        : summary.recent_events.map(event =>
          <article key={event.id} className={"severity-" + event.severity}>
            <span className="rrugc-event-dot" aria-hidden="true" />
            <div>
              <strong>{event.event_type.replaceAll("_", " ")}</strong>
              <p>{event.message}</p>
            </div>
            <time>{eventTime(event.created_at)}</time>
          </article>
        )}
    </div>
  </section>;
}

export function DeliveryOperationsPanel({
  onError,
}: {
  onError: (message: string) => void;
}) {
  const [summary, setSummary] = useState<DeliveryOperationsSummary | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh(signal?: AbortSignal) {
    const next = await getDeliveryOperationsSummary(signal);
    setSummary(next);
  }

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal).catch(reason => {
      if (!controller.signal.aborted) {
        onError(reason instanceof Error ? reason.message : "Unable to load delivery operations.");
      }
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh().catch(() => undefined);
    }, 10000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  async function runNow() {
    if (busy || !summary?.automation_enabled) return;
    setBusy(true);
    onError("");
    try {
      await runDeliveryMaintenance();
      await refresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Unable to queue delivery maintenance.");
    } finally {
      setBusy(false);
    }
  }

  return <DeliveryOperationsView
    summary={summary}
    busy={busy}
    onRun={() => void runNow()}
  />;
}
