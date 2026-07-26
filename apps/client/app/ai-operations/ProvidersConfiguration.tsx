import { useEffect, useMemo, useState } from "react";
import {
  fetchAiOperationsConfiguration, setAiProviderPaused, setGlobalAiEmergencyStop,
  setTenantAiPaused, updateAiBudget, updateAiDefaults, updateAiOperationsConfiguration,
  updateAiProvider, type AiOpsAudit, type AiOpsConfiguration,
  type AiOpsProvider, type AiOpsProviderBreakdown,
} from "../../features/ai_operations";
import { formatCost } from "./presentation";

export function ProvidersTab({ metrics }: { metrics: AiOpsProviderBreakdown[] }) {
  const state = useConfiguration();
  if (state.loading) return <ConfigurationLoading />;
  if (state.error || !state.value) return <ConfigurationError error={state.error} retry={state.reload} />;
  return <ProviderCards configuration={state.value} metrics={metrics} onChanged={state.apply} onReload={state.reload} />;
}

export function ConfigurationTab() {
  const state = useConfiguration();
  if (state.loading) return <ConfigurationLoading />;
  if (state.error || !state.value) return <ConfigurationError error={state.error} retry={state.reload} />;
  return <ConfigurationForm configuration={state.value} onChanged={state.apply} onReload={state.reload} />;
}

function useConfiguration() {
  const [value, setValue] = useState<AiOpsConfiguration | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    fetchAiOperationsConfiguration().then(result => { if (alive) setValue(result); })
      .catch(reason => { if (alive) setError(String(reason?.message || "Configuration could not be loaded")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [version]);
  return { value, loading, error, apply: setValue, reload: () => setVersion(item => item + 1) };
}

export function replaceProviderConfiguration(configuration: AiOpsConfiguration, provider: AiOpsProvider, changes: object): AiOpsConfiguration {
  return { ...configuration, providers: configuration.providers.map(item => item.id === provider ? { ...item, ...changes } : item) };
}

export function ProviderCards({ configuration, metrics, onChanged, onReload }: {
  configuration: AiOpsConfiguration; metrics: AiOpsProviderBreakdown[];
  onChanged: (value: AiOpsConfiguration) => void; onReload: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [confirmProvider, setConfirmProvider] = useState<AiOpsProvider | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [audit, setAudit] = useState<AiOpsAudit | null>(null);
  const metricByProvider = useMemo(() => new Map(configuration.providers.map(provider => {
    const rows = metrics.filter(row => row.provider === provider.id);
    const count = rows.reduce((sum, row) => sum + row.count, 0);
    const completed = rows.reduce((sum, row) => sum + row.completed, 0);
    const failed = rows.reduce((sum, row) => sum + row.failed, 0);
    return [provider.id, {
      count, success: completed + failed ? completed / (completed + failed) : 0,
      highestGroupedP95: Math.max(0, ...rows.map(row => row.p95_latency_ms || 0)),
      cost: rows.reduce((sum, row) => sum + row.estimated_cost_micros, 0),
      currency: rows[0]?.currency || "USD",
    }];
  })), [configuration, metrics]);

  async function optimistic(provider: AiOpsProvider, changes: object) {
    const before = configuration;
    const next = replaceProviderConfiguration(configuration, provider, changes);
    onChanged(next); setPending(provider); setError("");
    try {
      const result = await updateAiProvider(provider, { ...changes, reason: "AI Operations configuration update" });
      if (result.audit) setAudit(result.audit);
      onReload();
    } catch (reason) {
      onChanged(before);
      setError(String((reason as Error)?.message || "Provider update failed"));
    } finally { setPending(null); }
  }

  async function confirmPause() {
    if (!confirmProvider || !reason.trim()) return;
    const provider = configuration.providers.find(item => item.id === confirmProvider)!;
    setPending(confirmProvider); setError("");
    try {
      const result = await setAiProviderPaused(confirmProvider, !provider.paused, reason.trim());
      if (result.audit) setAudit(result.audit);
      setConfirmProvider(null); setReason(""); onReload();
    } catch (failure) { setError(String((failure as Error)?.message || "Pause update failed")); }
    finally { setPending(null); }
  }

  return <section className="ops-content" aria-labelledby="providers-title">
    <div className="ops-section-heading"><div><h2 id="providers-title">Providers</h2><p>Tenant controls and today’s provider health. Credentials are never displayed.</p></div></div>
    {error && <div className="ops-inline-error" role="alert">{error}</div>}
    {audit && <AuditNotice audit={audit} />}
    <div className="ops-provider-grid">{configuration.providers.map(provider => {
      const metric = metricByProvider.get(provider.id)!;
      const configureDisabled = pending === provider.id || !(configuration.permissions.can_configure_provider ?? configuration.permissions.can_manage_tenant);
      const pauseDisabled = pending === provider.id || !(configuration.permissions.can_emergency_stop ?? configuration.permissions.can_manage_tenant);
      return <article className="ops-provider-card" key={provider.id}>
        <header><div><h3>{provider.label}</h3><span className={`ops-connection ${provider.connection_configured ? "ok" : "off"}`}>{provider.connection_configured ? "Connection configured" : "Connection not configured"}</span></div><Status enabled={provider.enabled && provider.processing_enabled && !provider.paused} /></header>
        <dl>
          <div><dt>Single</dt><dd>{provider.single_enabled ? "Enabled" : "Disabled"}</dd></div>
          <div><dt>Batch</dt><dd>{provider.batch_enabled ? "Enabled" : "Disabled"}</dd></div>
          <div><dt>Default model</dt><dd>{provider.default_model || "Not set"}</dd></div>
          <div><dt>Allowed models</dt><dd>{provider.allowed_models.join(", ") || "None"}</dd></div>
          <div><dt>Requests today</dt><dd>{metric.count}</dd></div>
          <div><dt>Success rate</dt><dd>{(metric.success * 100).toFixed(1)}%</dd></div>
          <div><dt title="Maximum p95 among the provider/model/mode groups returned by the API">Highest grouped p95 latency</dt><dd>{Math.round(metric.highestGroupedP95)} ms</dd></div>
          <div><dt>Estimated cost today</dt><dd>{formatCost(metric.cost, metric.currency)}</dd></div>
          <div className="wide"><dt>Last error</dt><dd><code>{provider.last_error || "None"}</code></dd></div>
        </dl>
        <fieldset disabled={configureDisabled} className="ops-provider-switches"><legend>Tenant settings</legend>
          <label><input type="checkbox" checked={provider.processing_enabled} onChange={event => optimistic(provider.id, { processing_enabled: event.target.checked })} /> Provider enabled</label>
          <label><input type="checkbox" checked={provider.single_enabled} onChange={event => optimistic(provider.id, { single_enabled: event.target.checked })} /> Single enabled</label>
          <label><input type="checkbox" checked={provider.batch_enabled} onChange={event => optimistic(provider.id, { batch_enabled: event.target.checked })} /> Batch enabled</label>
        </fieldset>
        <button className={provider.paused ? "primary" : "danger"} type="button" disabled={pauseDisabled} onClick={() => { setConfirmProvider(provider.id); setReason(""); }}>{provider.paused ? "Resume provider" : "Pause provider"}</button>
        {confirmProvider === provider.id && <div className="ops-confirm" role="dialog" aria-label={`${provider.paused ? "Resume" : "Pause"} ${provider.label}`}>
          <strong>Confirm {provider.paused ? "resume" : "pause"}</strong><p>Queued work is preserved. A reason is required for the audit log.</p>
          <label>Reason<input autoFocus value={reason} onChange={event => setReason(event.target.value)} /></label>
          <div><button type="button" onClick={() => setConfirmProvider(null)}>Cancel</button><button type="button" className="danger" disabled={!reason.trim()} onClick={confirmPause}>Confirm</button></div>
        </div>}
      </article>;
    })}</div>
  </section>;
}

export function ConfigurationForm({ configuration, onChanged: _onChanged, onReload }: {
  configuration: AiOpsConfiguration; onChanged: (value: AiOpsConfiguration) => void; onReload: () => void;
}) {
  const [form, setForm] = useState(() => {
    const fallback = configuration.providers.find(item => item.connection_configured) || configuration.providers[0];
    return { ...configuration.tenant, default_provider: configuration.tenant.default_provider || fallback?.id || null, default_model: configuration.tenant.default_model || fallback?.default_model || null };
  });
  const [budget, setBudget] = useState(() => configuration.budget || { enabled: false, daily_limit_micros: null, monthly_limit_micros: null, warning_threshold_percent: 80, hard_stop_threshold_percent: 100, currency: "USD" });
  const [reason, setReason] = useState("");
  const [singleConcurrency, setSingleConcurrency] = useState(() => configuration.providers.find(item => item.id === configuration.tenant.default_provider)?.single_concurrency || 1);
  const [batchConcurrency, setBatchConcurrency] = useState(() => configuration.providers.find(item => item.id === configuration.tenant.default_provider)?.batch_concurrency || 1);
  const [error, setError] = useState("");
  const [audit, setAudit] = useState<AiOpsAudit | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"budget" | "tenant-stop" | "global-stop" | null>(null);
  const selectedProvider = configuration.providers.find(item => item.id === form.default_provider) || configuration.providers[0];
  const allowedModels = selectedProvider?.allowed_models || [];
  const canEdit = configuration.permissions.can_configure_provider ?? configuration.permissions.can_manage_tenant;
  const canUpdateBudget = configuration.permissions.can_update_budget ?? configuration.permissions.can_manage_tenant;
  const canEmergencyStop = configuration.permissions.can_emergency_stop ?? configuration.permissions.can_manage_tenant;

  async function saveConfiguration() {
    if (!reason.trim()) { setError("A reason is required for the audit log."); return; }
    if (!form.default_provider || !form.default_model || !allowedModels.includes(form.default_model)) { setError("Select an allowed provider model."); return; }
    setSaving(true); setError("");
    try {
      const defaults = await updateAiDefaults({ provider: form.default_provider, model: form.default_model, reason: reason.trim() });
      await updateAiProvider(form.default_provider, { single_active_jobs_limit: singleConcurrency, batch_active_jobs_limit: batchConcurrency, tenant_ai_active_jobs_limit: form.total_ai_concurrency, reason: reason.trim() });
      const result = await updateAiOperationsConfiguration({
        default_mode: form.default_mode, default_metadata_profile: form.default_metadata_profile,
        auto_analyze_new_assets: form.auto_analyze_new_assets, daily_item_limit: form.daily_item_limit,
        retry_count: form.retry_count, timeout_seconds: form.timeout_seconds, reason: reason.trim(),
      });
      setAudit((result.audit || defaults.audit) as AiOpsAudit); onReload();
    } catch (failure) { setError(String((failure as Error)?.message || "Configuration update failed")); }
    finally { setSaving(false); }
  }

  async function saveBudget() {
    if (!reason.trim()) { setError("A reason is required for a budget override."); return; }
    setSaving(true); setError("");
    try {
      const result = await updateAiBudget({ ...budget, reason: reason.trim() });
      if (result.audit) setAudit(result.audit); setConfirmAction(null); onReload();
    } catch (failure) { setError(String((failure as Error)?.message || "Budget update failed")); }
    finally { setSaving(false); }
  }

  async function toggleTenant() {
    if (!reason.trim()) { setError("A reason is required for an emergency action."); return; }
    setSaving(true);
    try { const result = await setTenantAiPaused(form.ai_enabled, reason.trim()); if (result.audit) setAudit(result.audit); setConfirmAction(null); onReload(); }
    catch (failure) { setError(String((failure as Error)?.message || "AI state update failed")); }
    finally { setSaving(false); }
  }

  async function toggleGlobal() {
    if (!reason.trim()) { setError("A reason is required for the global emergency stop."); return; }
    setSaving(true);
    try { await setGlobalAiEmergencyStop(!configuration.global.emergency_stop, reason.trim()); setAudit({ actor: "platform administrator", action: "global_ai_emergency_updated", reason: reason.trim(), timestamp: new Date().toISOString() }); setConfirmAction(null); onReload(); }
    catch (failure) { setError(String((failure as Error)?.message || "Global emergency update failed")); }
    finally { setSaving(false); }
  }

  return <section className="ops-content ops-configuration" aria-labelledby="configuration-title">
    <div className="ops-section-heading"><div><h2 id="configuration-title">Configuration</h2><p>Tenant settings are editable. Global upper bounds are deployment-managed and read-only.</p></div><span className="ops-scope">Tenant: {configuration.tenant_id}</span></div>
    {error && <div className="ops-inline-error" role="alert">{error}</div>}{audit && <AuditNotice audit={audit} />}
    <div className="ops-config-grid">
      <form className="ops-config-card" onSubmit={event => { event.preventDefault(); saveConfiguration(); }}>
        <header className="ops-config-card-header"><div><h3>Thiết lập mặc định</h3><p>Chọn cách hệ thống xử lý tài sản mới trong workspace này.</p></div><span className="ops-card-kicker">Tenant</span></header>
        <div className="ops-form-section">
          <div className="ops-form-section-heading"><h4>Nhà cung cấp &amp; mô hình</h4><p>Chỉ các nhà cung cấp đã kết nối và mô hình được phép mới có thể chọn.</p></div>
          <div className="ops-field-grid">
            <label>Default provider<select disabled={!canEdit} value={form.default_provider || selectedProvider?.id || ""} onChange={event => { const provider = configuration.providers.find(item => item.id === event.target.value)!; setForm({ ...form, default_provider: provider.id, default_model: provider.default_model }); setSingleConcurrency(provider.single_concurrency); setBatchConcurrency(provider.batch_concurrency); }}><option value="">Select provider</option>{configuration.providers.filter(item => item.connection_configured).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
            <label>Default model<select disabled={!canEdit} value={form.default_model || ""} onChange={event => setForm({ ...form, default_model: event.target.value })}><option value="">Select model</option>{allowedModels.map(model => <option key={model}>{model}</option>)}</select></label>
            <label>Default mode<select disabled={!canEdit} value={form.default_mode} onChange={event => setForm({ ...form, default_mode: event.target.value as "single" | "batch" })}>{selectedProvider?.single_enabled && <option value="single">Single</option>}{selectedProvider?.batch_enabled && <option value="batch">Batch</option>}</select><small>Single xử lý ngay; Batch phù hợp khi cần xử lý số lượng lớn.</small></label>
            <label>Default metadata profile<select disabled={!canEdit} value={form.default_metadata_profile || ""} onChange={event => setForm({ ...form, default_metadata_profile: event.target.value || null })}><option value="">Server default</option>{configuration.metadata_profiles.map(profile => <option key={profile}>{profile}</option>)}</select><small>Profile quyết định cấu trúc metadata được tạo.</small></label>
          </div>
        </div>
        <div className="ops-form-section">
          <div className="ops-form-section-heading"><h4>Tự động hóa &amp; tải xử lý</h4><p>Giới hạn đồng thời giúp bảo vệ quota và tránh làm nghẽn hàng đợi.</p></div>
          <label className="check ops-field-full"><input disabled={!canEdit || !configuration.global.ai_auto_analyze_enabled} type="checkbox" checked={form.auto_analyze_new_assets} onChange={event => setForm({ ...form, auto_analyze_new_assets: event.target.checked })} /> Tự động phân tích tài sản mới</label>
          {!configuration.global.ai_auto_analyze_enabled && <small className="ops-field-note">Tính năng này đang bị giới hạn ở cấu hình triển khai toàn cục.</small>}
          <div className="ops-field-grid">
            <label>Single concurrency<input disabled={!canEdit} type="number" min="1" max="100" value={singleConcurrency} onChange={event => setSingleConcurrency(Number(event.target.value))} /></label>
            <label>Batch concurrency<input disabled={!canEdit} type="number" min="1" max="100" value={batchConcurrency} onChange={event => setBatchConcurrency(Number(event.target.value))} /></label>
            <label>Total AI concurrency<input disabled={!canEdit} type="number" min="1" max="500" value={form.total_ai_concurrency} onChange={event => setForm({ ...form, total_ai_concurrency: Number(event.target.value) })} /><small>Tổng tác vụ AI chạy đồng thời trong tenant.</small></label>
            <label>Daily item limit<input disabled={!canEdit} type="number" min="1" max="10000" value={form.daily_item_limit} onChange={event => setForm({ ...form, daily_item_limit: Number(event.target.value) })} /><small>Số tài sản tối đa được xử lý mỗi ngày.</small></label>
            <label>Retry count<input disabled={!canEdit} type="number" min="0" max="20" value={form.retry_count} onChange={event => setForm({ ...form, retry_count: Number(event.target.value) })} /></label>
            <label>Timeout (seconds)<input disabled={!canEdit} type="number" min="1" max="3600" value={form.timeout_seconds} onChange={event => setForm({ ...form, timeout_seconds: Number(event.target.value) })} /></label>
          </div>
        </div>
        <div className="ops-form-footer">
          <label>Change reason<input disabled={!canEdit} required value={reason} onChange={event => setReason(event.target.value)} placeholder="Ví dụ: tăng giới hạn xử lý cho chiến dịch tháng 7" /><small>Lý do được lưu trong nhật ký kiểm toán.</small></label>
          <button className="primary" disabled={!canEdit || saving} type="submit">Save tenant defaults</button>
        </div>
      </form>
      {configuration.permissions.can_read_budget !== false ? <form className="ops-config-card" onSubmit={event => { event.preventDefault(); setConfirmAction("budget"); }}>
        <header className="ops-config-card-header"><div><h3>Chính sách ngân sách</h3><p>Đặt ngưỡng chi phí AI cho tenant. Mọi thay đổi đều cần xác nhận.</p></div><span className="ops-card-kicker">Budget</span></header>
        <label className="check ops-field-full"><input disabled={!canUpdateBudget} type="checkbox" checked={budget.enabled} onChange={event => setBudget({ ...budget, enabled: event.target.checked })} /> Bật kiểm soát ngân sách</label>
        <div className="ops-form-section">
          <div className="ops-form-section-heading"><h4>Hạn mức chi phí</h4><p>Đơn vị micro theo loại tiền tệ được cấu hình ở máy chủ.</p></div>
          <div className="ops-field-grid">
            <label>Daily budget (micros)<input disabled={!canUpdateBudget} type="number" min="0" value={budget.daily_limit_micros ?? ""} onChange={event => setBudget({ ...budget, daily_limit_micros: event.target.value ? Number(event.target.value) : null })} /><small>Ngân sách tối đa theo ngày.</small></label>
            <label>Monthly budget (micros)<input disabled={!canUpdateBudget} type="number" min="0" value={budget.monthly_limit_micros ?? ""} onChange={event => setBudget({ ...budget, monthly_limit_micros: event.target.value ? Number(event.target.value) : null })} /><small>Ngân sách tối đa theo tháng.</small></label>
          </div>
        </div>
        <div className="ops-form-section">
          <div className="ops-form-section-heading"><h4>Ngưỡng cảnh báo</h4><p>Hệ thống cảnh báo trước khi chạm ngưỡng dừng cứng.</p></div>
          <div className="ops-field-grid">
            <label>Warning threshold (%)<input disabled={!canUpdateBudget} type="number" min="0" max="100" value={budget.warning_threshold_percent} onChange={event => setBudget({ ...budget, warning_threshold_percent: Number(event.target.value) })} /><small>Gửi cảnh báo khi đạt tỷ lệ này.</small></label>
            <label>Hard-stop threshold (%)<input disabled={!canUpdateBudget} type="number" min="1" max="100" value={budget.hard_stop_threshold_percent} onChange={event => setBudget({ ...budget, hard_stop_threshold_percent: Number(event.target.value) })} /><small>Chặn tác vụ AI mới khi đạt tỷ lệ này.</small></label>
          </div>
        </div>
        <button className="primary ops-form-submit" disabled={!canUpdateBudget || saving} type="submit">Review budget update</button>
      </form> : <section className="ops-config-card"><h3>Budget policy</h3><small>Permission ai_budget.read is required to view budget settings.</small></section>}
      <section className="ops-global-settings"><header className="ops-config-card-header"><div><h3>Global controls</h3><p>Giới hạn toàn cục do deployment quản lý và chỉ có thể xem tại đây.</p></div><span className="ops-card-kicker">Read-only</span></header><dl><div><dt>Single pipeline</dt><dd>{configuration.global.single_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Batch pipeline</dt><dd>{configuration.global.batch_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Global emergency stop</dt><dd>{configuration.global.emergency_stop ? "Active" : "Inactive"}</dd></div></dl><p>Tenant không thể bật lại chức năng đã bị tắt ở cấp toàn cục.</p>
        {configuration.permissions.can_manage_global ? <button type="button" className="danger" onClick={() => setConfirmAction("global-stop")}>{configuration.global.emergency_stop ? "Resume global AI" : "Emergency stop all AI"}</button> : <small>Chỉ Platform administrator mới có thể thay đổi cấu hình toàn cục.</small>}
        <button type="button" className={form.ai_enabled ? "danger" : "primary"} disabled={!canEmergencyStop} onClick={() => setConfirmAction("tenant-stop")}>{form.ai_enabled ? "Pause tenant AI" : "Resume tenant AI"}</button>
      </section>
    </div>
    {confirmAction && <div className="ops-confirm ops-confirm-wide" role="dialog" aria-label="Confirm configuration change"><h3>Confirm {confirmAction === "budget" ? "budget override" : "emergency action"}</h3><p>This action is audited. Enter a reason before continuing.</p><label>Reason<input autoFocus value={reason} onChange={event => setReason(event.target.value)} /></label><div><button type="button" onClick={() => setConfirmAction(null)}>Cancel</button><button className="danger" type="button" disabled={!reason.trim() || saving} onClick={confirmAction === "budget" ? saveBudget : confirmAction === "global-stop" ? toggleGlobal : toggleTenant}>Confirm</button></div></div>}
  </section>;
}

function AuditNotice({ audit }: { audit: AiOpsAudit }) { return <div className="ops-audit" role="status"><strong>Audit recorded</strong><span>{audit.action} · {audit.reason}</span><time dateTime={audit.timestamp}>{new Date(audit.timestamp).toLocaleString()}</time></div>; }
function Status({ enabled }: { enabled: boolean }) { return <span className={`ops-provider-state ${enabled ? "enabled" : "disabled"}`}>{enabled ? "Enabled" : "Disabled"}</span>; }
function ConfigurationLoading() { return <div className="ops-skeleton" aria-busy="true"><i /><i /><span>Loading provider configuration…</span></div>; }
function ConfigurationError({ error, retry }: { error: string; retry: () => void }) { return <div className="ops-state unauthorized" role="alert"><strong>Configuration unavailable</strong><p>{error}</p><button type="button" onClick={retry}>Retry</button></div>; }
