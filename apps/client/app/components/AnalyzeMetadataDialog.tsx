import { useEffect, useMemo, useState } from "react";
import {
  fetchAiCapabilities,
  fetchAssetAnalysisStatus,
  fetchAnalysisRequestStatus,
  submitAnalysis,
} from "../../features/metadata";
import type {
  AiAnalysisSelection,
  AiCapabilities,
  AiProcessingMode,
  AiProviderCapability,
  AiProviderId,
  AnalysisProgress,
  AnalysisSubmission,
} from "../../features/metadata";
import type { Provider } from "../types";

type Props = {
  open: boolean;
  assetIds: string[];
  sourceProvider: Provider;
  authorized: boolean;
  defaultProfile?: string;
  defaultProfileVersion?: string | null;
  forceInitially?: boolean;
  includeProviderBatchId?: boolean;
  onClose: () => void;
  onSubmitted?: () => void;
};

export function enabledProviders(capabilities: AiCapabilities): AiProviderCapability[] {
  return capabilities.providers.filter(provider => provider.enabled && provider.supported_modes.length > 0);
}

export function availableModes(provider: AiProviderCapability): AiProcessingMode[] {
  return provider.supported_modes.filter(mode => provider.models.some(model => (
    mode === "single" ? model.supports_single : model.supports_batch
  )));
}

export function availableModels(provider: AiProviderCapability, mode: AiProcessingMode) {
  return provider.models.filter(model => mode === "single" ? model.supports_single : model.supports_batch);
}

export function initialAnalysisSelection(
  capabilities: AiCapabilities,
  assetCount: number,
): AiAnalysisSelection | null {
  const preferredMode: AiProcessingMode = assetCount > 1 ? "batch" : "single";
  const providers = enabledProviders(capabilities);
  const provider = providers.find(item => availableModes(item).includes(preferredMode)) || providers[0];
  if (!provider) return null;
  const modes = availableModes(provider);
  const processingMode = modes.includes(preferredMode) ? preferredMode : modes[0];
  if (!processingMode) return null;
  const models = availableModels(provider, processingMode);
  const model = models.find(item => item.id === provider.default_model) || models[0];
  return model ? { provider: provider.id, processingMode, model: model.id } : null;
}

export function retainExplicitSelection(
  capabilities: AiCapabilities,
  current: AiAnalysisSelection,
  assetCount: number,
): AiAnalysisSelection | null {
  const provider = enabledProviders(capabilities).find(item => item.id === current.provider);
  if (
    provider
    && availableModes(provider).includes(current.processingMode)
    && availableModels(provider, current.processingMode).some(model => model.id === current.model)
  ) return current;
  return initialAnalysisSelection(capabilities, assetCount);
}

export function forceConfirmationMessage(selection: AiAnalysisSelection): string {
  return `Force analysis preserves all previous analysis history. Continue with ${providerLabel(selection.provider)} / ${selection.model}?`;
}

export function providerLabel(provider: string): string {
  if (provider === "gemini") return "Google Gemini";
  if (provider === "openai") return "OpenAI";
  return provider || "Unknown provider";
}

function initialProgress(result: AnalysisSubmission): AnalysisProgress {
  if (result.kind === "single") return {
    provider: result.provider,
    model: result.model,
    processingMode: result.processing_mode,
    accepted: 1,
    queued: 1,
    running: 0,
    completed: 0,
    failed: 0,
    budgetBlocked: 0,
  };
  return {
    provider: result.provider,
    model: result.model,
    processingMode: result.processing_mode,
    accepted: result.analysis_count,
    queued: result.analysis_count,
    running: 0,
    completed: 0,
    failed: result.items.filter(item => !["accepted", "already_exists"].includes(item.acceptance_status)).length,
    budgetBlocked: result.items.filter(item => item.acceptance_status === "budget_preflight_failed").length,
  };
}

export function CapabilitiesState({
  loading,
  error,
  capabilities,
}: {
  loading: boolean;
  error: string;
  capabilities: AiCapabilities | null;
}) {
  if (loading) return <p className="analysis-dialog-state" role="status">Loading AI providers...</p>;
  if (error) return <div className="analysis-error-summary" role="alert"><b>AI options could not be loaded.</b><span>{error}</span></div>;
  if (capabilities && enabledProviders(capabilities).length === 0) {
    return <div className="analysis-empty" role="status"><b>No AI provider is enabled</b><span>Ask an administrator to enable a provider for this tenant.</span></div>;
  }
  return null;
}

export function AnalysisFields({
  capabilities,
  selection,
  disabled,
  profile,
  profileVersion,
  force,
  onProfile,
  onProfileVersion,
  onProvider,
  onMode,
  onModel,
  onForce,
}: {
  capabilities: AiCapabilities;
  selection: AiAnalysisSelection;
  disabled: boolean;
  profile: string;
  profileVersion: string;
  force: boolean;
  onProfile: (value: string) => void;
  onProfileVersion: (value: string) => void;
  onProvider: (value: AiProviderId) => void;
  onMode: (value: AiProcessingMode) => void;
  onModel: (value: string) => void;
  onForce: (value: boolean) => void;
}) {
  const providers = enabledProviders(capabilities);
  const provider = providers.find(item => item.id === selection.provider) || providers[0];
  const modes = availableModes(provider);
  const models = availableModels(provider, selection.processingMode);
  return <fieldset disabled={disabled} className="analysis-fields">
    <label htmlFor="analysis-profile">Metadata profile</label>
    <input id="analysis-profile" value={profile} required onChange={event => onProfile(event.target.value)} placeholder="e.g. creative-assets" />
    <label htmlFor="analysis-profile-version">Profile version <span>(optional)</span></label>
    <input id="analysis-profile-version" value={profileVersion} onChange={event => onProfileVersion(event.target.value)} placeholder="Active version" />
    <label htmlFor="analysis-provider">AI provider</label>
    <select id="analysis-provider" value={selection.provider} onChange={event => onProvider(event.target.value as AiProviderId)}>
      {providers.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select>
    <label htmlFor="analysis-mode">Processing mode</label>
    <select id="analysis-mode" value={selection.processingMode} onChange={event => onMode(event.target.value as AiProcessingMode)}>
      {modes.map(mode => <option key={mode} value={mode}>{mode === "single" ? "Single" : "Batch"}</option>)}
    </select>
    <label htmlFor="analysis-model">Model</label>
    <select id="analysis-model" value={selection.model} onChange={event => onModel(event.target.value)}>
      {models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
    </select>
    <label className="analysis-force"><input type="checkbox" checked={force} onChange={event => onForce(event.target.checked)} />Force a new analysis</label>
  </fieldset>;
}

export function AnalysisProgressView({ progress }: { progress: AnalysisProgress }) {
  const terminal = progress.completed + progress.failed;
  const percent = progress.accepted ? Math.min(100, Math.round(terminal / progress.accepted * 100)) : 0;
  return <section className="analysis-progress-card" aria-live="polite" aria-label="Analysis progress">
    <header><div><b>{providerLabel(progress.provider)}</b><span>{progress.model}</span></div><em>{progress.processingMode}</em></header>
    <div className="analysis-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }} /></div>
    <dl>
      <div><dt>Accepted</dt><dd>{progress.accepted}</dd></div><div><dt>Queued</dt><dd>{progress.queued}</dd></div>
      <div><dt>Running</dt><dd>{progress.running}</dd></div><div><dt>Completed</dt><dd>{progress.completed}</dd></div>
      <div><dt>Failed</dt><dd>{progress.failed}</dd></div><div><dt>Budget blocked</dt><dd>{progress.budgetBlocked}</dd></div>
    </dl>
    {progress.providerBatchStatus && <p>Provider batch: {progress.providerBatchStatus}</p>}
  </section>;
}

export function BatchWarning({ assetCount }: { assetCount: number }) {
  return <div className="analysis-warning" role="note">
    <b>Batch processing is asynchronous</b>
    <span>It may start and finish later than Single processing.</span>
    {assetCount === 1 && <span>A one-asset batch is allowed, but still has delayed completion.</span>}
  </div>;
}

export function AnalyzeMetadataDialog(props: Props) {
  const [capabilities, setCapabilities] = useState<AiCapabilities | null>(null);
  const [selection, setSelection] = useState<AiAnalysisSelection | null>(null);
  const [profile, setProfile] = useState(props.defaultProfile || "");
  const [profileVersion, setProfileVersion] = useState(props.defaultProfileVersion || "");
  const [force, setForce] = useState(Boolean(props.forceInitially));
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [submission, setSubmission] = useState<AnalysisSubmission | null>(null);
  const [progress, setProgress] = useState<AnalysisProgress | null>(null);

  useEffect(() => {
    if (!props.open) return;
    setProfile(props.defaultProfile || "");
    setProfileVersion(props.defaultProfileVersion || "");
    setForce(Boolean(props.forceInitially));
    setError(""); setSubmission(null); setProgress(null); setLoading(true);
    const controller = new AbortController();
    void fetchAiCapabilities(controller.signal).then(value => {
      setCapabilities(value);
      setSelection(current => current
        ? retainExplicitSelection(value, current, props.assetIds.length)
        : initialAnalysisSelection(value, props.assetIds.length));
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Capabilities request failed");
    }).finally(() => !controller.signal.aborted && setLoading(false));
    return () => controller.abort();
  }, [props.open]);

  useEffect(() => {
    if (!props.open || !submission) return;
    const controller = new AbortController();
    let timer = 0;
    const poll = async () => {
      try {
        if (submission.kind === "bulk") {
          const status = await fetchAnalysisRequestStatus(submission.request_id, Boolean(props.includeProviderBatchId), controller.signal);
          const budgetBlocked = status.items.filter(item => item.acceptance_status === "budget_preflight_failed" || item.error_code === "budget_blocked").length;
          const providerBatch = status.items.find(item => item.provider_batch_id)?.provider_batch_id;
          setProgress({ provider: status.provider, model: status.model, processingMode: status.processing_mode, accepted: status.analysis_count, queued: status.queued, running: status.running, completed: status.completed, failed: status.failed, budgetBlocked, providerBatchStatus: providerBatch || (status.batch_count ? status.status : undefined) });
          if (!["completed", "partial_failed", "cancelled"].includes(status.status)) timer = window.setTimeout(poll, 1500);
        } else {
          const analysis = await fetchAssetAnalysisStatus(props.assetIds[0], submission.analysis_id, controller.signal);
          const status = String(analysis?.status || "pending");
          const terminal = ["completed", "failed", "budget_blocked"].includes(status);
          setProgress({ provider: submission.provider, model: submission.model, processingMode: submission.processing_mode, accepted: 1, queued: status === "pending" ? 1 : 0, running: status === "running" ? 1 : 0, completed: status === "completed" ? 1 : 0, failed: ["failed", "budget_blocked"].includes(status) ? 1 : 0, budgetBlocked: status === "budget_blocked" ? 1 : 0 });
          if (!terminal) timer = window.setTimeout(poll, 1500);
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to refresh analysis progress");
      }
    };
    timer = window.setTimeout(poll, 500);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [props.open, submission, props.includeProviderBatchId]);

  const provider = useMemo(() => capabilities && selection
    ? enabledProviders(capabilities).find(item => item.id === selection.provider)
    : null, [capabilities, selection]);

  function changeProvider(id: AiProviderId) {
    if (!capabilities || !selection) return;
    const nextProvider = enabledProviders(capabilities).find(item => item.id === id);
    if (!nextProvider) return;
    const modes = availableModes(nextProvider);
    const mode = modes.includes(selection.processingMode) ? selection.processingMode : modes[0];
    const models = availableModels(nextProvider, mode);
    const model = models.find(item => item.id === nextProvider.default_model) || models[0];
    if (model) setSelection({ provider: id, processingMode: mode, model: model.id });
  }

  function changeMode(mode: AiProcessingMode) {
    if (!provider || !selection || !availableModes(provider).includes(mode)) return;
    const models = availableModels(provider, mode);
    const model = models.find(item => item.id === selection.model)
      || models.find(item => item.id === provider.default_model) || models[0];
    if (model) setSelection({ ...selection, processingMode: mode, model: model.id });
  }

  async function submit() {
    if (!selection || !profile.trim() || !props.authorized || !props.assetIds.length) return;
    if (force && !window.confirm(forceConfirmationMessage(selection))) return;
    setSubmitting(true); setError("");
    try {
      const result = await submitAnalysis({
        assetIds: props.assetIds,
        sourceProvider: props.sourceProvider,
        metadataProfile: profile.trim(),
        metadataProfileVersion: profileVersion.trim() || null,
        provider: selection.provider,
        processingMode: selection.processingMode,
        model: selection.model,
        force,
      });
      setSubmission(result); setProgress(initialProgress(result)); props.onSubmitted?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Analysis request failed");
    } finally { setSubmitting(false); }
  }

  if (!props.open) return null;
  const disabledReason = !props.authorized ? "You are not authorized to analyze these assets."
    : !props.assetIds.length ? "Select at least one indexed file. Folders cannot be analyzed." : "";
  const budgetMessage = submission?.kind === "bulk"
    ? submission.items.find(item => item.acceptance_status === "budget_preflight_failed")?.error_message
    : null;
  return <div className="analysis-dialog-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && props.onClose()}>
    <div className="analysis-dialog" role="dialog" aria-modal="true" aria-labelledby="analysis-dialog-title" aria-describedby="analysis-dialog-description" onKeyDown={event => event.key === "Escape" && props.onClose()}>
      <header><div><small>AI metadata</small><h2 id="analysis-dialog-title">Analyze metadata</h2></div><button type="button" onClick={props.onClose} aria-label="Close analysis dialog">x</button></header>
      <p id="analysis-dialog-description">Analyze {props.assetIds.length} selected asset{props.assetIds.length === 1 ? "" : "s"} with an enabled server provider.</p>
      {error && <div className="analysis-error-summary" role="alert"><b>Analysis request failed</b><span>{error}</span></div>}
      {disabledReason && <div className="analysis-disabled" role="note">{disabledReason}</div>}
      <CapabilitiesState loading={loading} error={!loading && !capabilities ? error : ""} capabilities={capabilities} />
      {capabilities && selection && <>
        <AnalysisFields capabilities={capabilities} selection={selection} disabled={submitting || Boolean(disabledReason)} profile={profile} profileVersion={profileVersion} force={force} onProfile={setProfile} onProfileVersion={setProfileVersion} onProvider={changeProvider} onMode={changeMode} onModel={model => setSelection({ ...selection, model })} onForce={setForce} />
        {selection.processingMode === "batch" && <BatchWarning assetCount={props.assetIds.length} />}
        {force && <div className="analysis-warning" role="note">Previous analysis history will be preserved. The selected {providerLabel(selection.provider)} model ({selection.model}) will be used.</div>}
      </>}
      {budgetMessage && <div className="analysis-budget-warning" role="alert"><b>Budget preflight warning</b><span>{budgetMessage}</span></div>}
      {progress && <AnalysisProgressView progress={progress} />}
      <footer><button type="button" className="secondary" onClick={props.onClose}>{submission ? "Close" : "Cancel"}</button>{!submission && <button type="button" disabled={submitting || loading || !selection || !profile.trim() || Boolean(disabledReason)} onClick={() => void submit()}>{submitting ? "Submitting..." : force ? "Confirm force analysis" : "Analyze metadata"}</button>}</footer>
    </div>
  </div>;
}
