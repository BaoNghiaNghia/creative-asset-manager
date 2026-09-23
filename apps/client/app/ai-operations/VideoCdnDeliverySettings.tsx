import { useCallback, useEffect, useState } from "react";
import { ConfigurationCardHeader } from "./ConfigurationCardHeader";
import {
  fetchVideoCdnDeliveryObservability,
  fetchVideoCdnDeliveryRuntimeStatus,
  updateVideoCdnDeliveryRuntime,
  type VideoCdnDeliveryObservability,
  type VideoCdnDeliveryRuntimeStatus,
} from "../../features/settings";

function yesNo(value: boolean, ready = "Ready", missing = "Missing") {
  return value ? ready : missing;
}

function rolloutLabel(status: VideoCdnDeliveryRuntimeStatus) {
  if (status.rollout_mode === "canary") return `Canary · ${status.canary_tenant_count} tenant${status.canary_tenant_count === 1 ? "" : "s"}`;
  if (status.rollout_mode === "global") return "Global";
  return "Disabled";
}

function fallbackTotal(observability: VideoCdnDeliveryObservability | null) {
  if (!observability) return 0;
  return Object.entries(observability.metrics.counters)
    .filter(([name]) => name.startsWith("video_cdn_fallback_"))
    .reduce((sum, [, count]) => sum + count, 0);
}

export function VideoCdnDeliverySettingsView({
  status,
  observability,
  reason,
  busy,
  error,
  notice,
  onReasonChange,
  onToggle,
  onRefresh,
}: {
  status: VideoCdnDeliveryRuntimeStatus;
  observability: VideoCdnDeliveryObservability | null;
  reason: string;
  busy: boolean;
  error: string;
  notice: string;
  onReasonChange: (value: string) => void;
  onToggle: () => void;
  onRefresh: () => void;
}) {
  const enabling = !status.runtime_enabled;
  const prerequisiteBlocked = enabling && !status.can_enable;
  const redirects = observability?.metrics.counters.video_cdn_redirect_total ?? 0;
  const probeFailures = observability?.metrics.counters.video_cdn_probe_failure_total ?? 0;
  const guard = observability?.guard;

  return <section className="ops-config-card ops-config-video-cdn" aria-labelledby="video-cdn-delivery-title">
    <ConfigurationCardHeader
      icon="video-cdn-activation"
      title="Video CDN activation"
      titleId="video-cdn-delivery-title"
      description="Production activation console for original-quality Public Review video delivery through the signed Cloudflare path."
      kicker="Platform"
    />

    <div className="ops-video-cdn-state" aria-label="Video CDN activation state">
      <span className={status.effective_enabled ? "ok" : "off"}>
        {status.effective_enabled ? "DELIVERY ACTIVE" : "DELIVERY INACTIVE"}
      </span>
      <small>Rollout: {rolloutLabel(status)}</small>
    </div>

    <dl className="ops-video-cdn-prerequisites">
      <div><dt>Runtime gate</dt><dd>{status.runtime_enabled ? "Enabled" : "Disabled"}</dd></div>
      <div><dt>R2 cache</dt><dd>{yesNo(status.prerequisites.r2_video_cache_enabled, "Ready", "Disabled")}</dd></div>
      <div><dt>Signed delivery</dt><dd>{yesNo(status.prerequisites.delivery_configured, "Configured")}</dd></div>
      <div><dt>Rollout scope</dt><dd>{yesNo(status.prerequisites.rollout_scope_configured, rolloutLabel(status))}</dd></div>
      <div><dt>Health guard</dt><dd>{yesNo(status.prerequisites.delivery_guard_enabled, guard?.state || "Enabled", "Disabled")}</dd></div>
      <div><dt>Can enable</dt><dd>{status.can_enable ? "Yes" : "No"}</dd></div>
    </dl>

    <section className="ops-video-cdn-observability" aria-label="Video CDN process observability">
      <div><span>Redirects</span><strong>{redirects}</strong></div>
      <div><span>Provider fallbacks</span><strong>{fallbackTotal(observability)}</strong></div>
      <div><span>Probe failures</span><strong>{probeFailures}</strong></div>
      <div><span>Decision p95</span><strong>{observability?.metrics.decision_latency_ms.p95 == null ? "—" : `${Math.round(observability.metrics.decision_latency_ms.p95)} ms`}</strong></div>
    </section>

    <div className="ops-video-cdn-guard">
      <strong>Circuit breaker</strong>
      <span>{guard ? guard.state : "Unavailable"}</span>
      {guard && <small>
        {guard.consecutive_failures}/{guard.failure_threshold} failures · probe every {guard.probe_interval_seconds}s · cooldown {guard.cooldown_seconds}s
        {guard.open_remaining_seconds > 0 ? ` · ${Math.ceil(guard.open_remaining_seconds)}s remaining` : ""}
      </small>}
    </div>

    {status.blockers.length > 0 && <div className="ops-video-cdn-blockers" role="status">
      <strong>Activation blockers</strong>
      <ul>{status.blockers.map(blocker => <li key={blocker}>{blocker.replaceAll("_", " ")}</li>)}</ul>
    </div>}

    <p className="ops-video-cdn-note">
      Metrics and circuit state are process-local rollout evidence. Disabling the persisted runtime gate remains the global rollback control.
    </p>

    {prerequisiteBlocked && <div className="ops-inline-error" role="status">
      Activation is blocked until R2 cache, signed delivery, rollout scope, and the delivery health guard are all ready.
    </div>}
    {error && <div className="ops-inline-error" role="alert">{error}</div>}
    {notice && <div className="ops-audit" role="status">{notice}</div>}

    <div className="ops-video-cdn-actions">
      <label className="ops-field-full">
        Change reason
        <input
          value={reason}
          maxLength={500}
          disabled={busy}
          onChange={event => onReasonChange(event.target.value)}
          placeholder="Ví dụ: enable approved tenant canary after preflight"
        />
        <small>This reason is stored in the audit log. No tenant IDs, signed URLs, or secrets are shown here.</small>
      </label>
      <div>
        <button type="button" disabled={busy} onClick={onRefresh}>Refresh status</button>
        <button
          type="button"
          className={status.runtime_enabled ? "danger" : "primary"}
          aria-pressed={status.runtime_enabled}
          disabled={busy || reason.trim().length < 3 || prerequisiteBlocked}
          onClick={onToggle}
        >
          {busy
            ? "Saving…"
            : status.runtime_enabled
              ? "Disable CDN delivery"
              : status.rollout_mode === "canary"
                ? "Enable canary delivery"
                : "Enable CDN delivery"}
        </button>
      </div>
    </div>
  </section>;
}

export function VideoCdnDeliverySettings() {
  const [status, setStatus] = useState<VideoCdnDeliveryRuntimeStatus | null>(null);
  const [observability, setObservability] = useState<VideoCdnDeliveryObservability | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextStatus = await fetchVideoCdnDeliveryRuntimeStatus();
      setStatus(nextStatus);
      try {
        setObservability(await fetchVideoCdnDeliveryObservability());
      } catch {
        setObservability(null);
        setError("Process-local observability is unavailable. Runtime rollback remains available.");
      }
    } catch (failure) {
      setStatus(null);
      setObservability(null);
      setError(String((failure as Error)?.message || "Video CDN activation status is unavailable."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function toggle() {
    if (!status || reason.trim().length < 3) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await updateVideoCdnDeliveryRuntime(
        !status.runtime_enabled,
        reason.trim(),
      );
      setStatus(next);
      setReason("");
      setNotice(next.runtime_enabled
        ? "Video CDN runtime gate enabled for the configured rollout scope."
        : "Video CDN runtime gate disabled; Public Review falls back to the source provider.");
      try {
        setObservability(await fetchVideoCdnDeliveryObservability());
      } catch {
        setObservability(null);
      }
    } catch (failure) {
      setError(String((failure as Error)?.message || "Video CDN delivery update failed."));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <section className="ops-config-card ops-config-video-cdn" aria-busy="true" aria-live="polite">
      <ConfigurationCardHeader
        icon="video-cdn-activation"
        title="Video CDN activation"
        description="Loading runtime, rollout, and circuit-breaker status…"
        kicker="Platform"
      />
    </section>;
  }

  if (!status) {
    return <section className="ops-config-card ops-config-video-cdn" role="alert">
      <ConfigurationCardHeader
        icon="video-cdn-activation"
        title="Video CDN activation"
        description={error || "Video CDN activation status is unavailable."}
        kicker="Platform"
      />
      <button type="button" onClick={() => { void load(); }}>Retry</button>
    </section>;
  }

  return <VideoCdnDeliverySettingsView
    status={status}
    observability={observability}
    reason={reason}
    busy={busy}
    error={error}
    notice={notice}
    onReasonChange={setReason}
    onToggle={() => { void toggle(); }}
    onRefresh={() => { void load(); }}
  />;
}
