import { useCallback, useEffect, useState } from "react";
import {
  fetchVideoCdnDeliveryRuntimeStatus,
  updateVideoCdnDeliveryRuntime,
  type VideoCdnDeliveryRuntimeStatus,
} from "../../features/settings";

export function VideoCdnDeliverySettingsView({
  status,
  reason,
  busy,
  error,
  notice,
  onReasonChange,
  onToggle,
}: {
  status: VideoCdnDeliveryRuntimeStatus;
  reason: string;
  busy: boolean;
  error: string;
  notice: string;
  onReasonChange: (value: string) => void;
  onToggle: () => void;
}) {
  const enabling = !status.runtime_enabled;
  const prerequisiteBlocked = enabling && !status.can_enable;
  return <section className="ops-config-card ops-config-video-cdn" aria-labelledby="video-cdn-delivery-title">
    <header className="ops-config-card-header">
      <div>
        <h3 id="video-cdn-delivery-title">Video CDN delivery</h3>
        <p>Global rollout gate for original-quality video delivery through the signed Cloudflare path.</p>
      </div>
      <span className="ops-card-kicker">Platform</span>
    </header>
    <dl>
      <div><dt>Runtime toggle</dt><dd>{status.runtime_enabled ? "Enabled" : "Disabled"}</dd></div>
      <div><dt>R2 video cache</dt><dd>{status.prerequisites.r2_video_cache_enabled ? "Ready" : "Disabled"}</dd></div>
      <div><dt>Signed delivery</dt><dd>{status.prerequisites.delivery_configured ? "Configured" : "Missing"}</dd></div>
      <div><dt>Effective delivery</dt><dd>{status.effective_enabled ? "Active" : "Inactive"}</dd></div>
    </dl>
    <p>
      Phase 4A only stores runtime intent. Public Review playback remains on the existing source path.
    </p>
    {prerequisiteBlocked && <div className="ops-inline-error" role="status">
      Server prerequisites are not ready. Configure and enable the R2 cache plus signed delivery before enabling this gate.
    </div>}
    {error && <div className="ops-inline-error" role="alert">{error}</div>}
    {notice && <div role="status">{notice}</div>}
    <label className="ops-field-full">
      Change reason
      <input
        value={reason}
        maxLength={500}
        disabled={busy}
        onChange={event => onReasonChange(event.target.value)}
        placeholder="Ví dụ: enable Phase 4A canary after prerequisites are verified"
      />
      <small>This reason is stored in the audit log. No secret values are shown here.</small>
    </label>
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
          : "Enable CDN delivery"}
    </button>
  </section>;
}

export function VideoCdnDeliverySettings() {
  const [status, setStatus] = useState<VideoCdnDeliveryRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await fetchVideoCdnDeliveryRuntimeStatus());
    } catch (failure) {
      setStatus(null);
      setError(String((failure as Error)?.message || "Video CDN delivery settings are unavailable."));
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
        ? "Video CDN delivery runtime gate enabled."
        : "Video CDN delivery runtime gate disabled.");
    } catch (failure) {
      setError(String((failure as Error)?.message || "Video CDN delivery update failed."));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <section className="ops-config-card ops-config-video-cdn" aria-busy="true" aria-live="polite">
      <h3>Video CDN delivery</h3>
      <p>Loading video CDN delivery settings…</p>
    </section>;
  }

  if (!status) {
    return <section className="ops-config-card ops-config-video-cdn" role="alert">
      <h3>Video CDN delivery</h3>
      <p>{error || "Video CDN delivery settings are unavailable."}</p>
      <button type="button" onClick={() => { void load(); }}>Retry</button>
    </section>;
  }

  return <VideoCdnDeliverySettingsView
    status={status}
    reason={reason}
    busy={busy}
    error={error}
    notice={notice}
    onReasonChange={setReason}
    onToggle={() => { void toggle(); }}
  />;
}
