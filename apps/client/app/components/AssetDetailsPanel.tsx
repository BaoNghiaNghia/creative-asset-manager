import { useEffect, useState } from "react";
import type { AssetDetails } from "../types";
import { AnalyzeMetadataDialog } from "./AnalyzeMetadataDialog";
import { AnalysisHistoryCard } from "./AnalysisHistoryCard";
import { SafeJsonTree } from "./SafeJsonTree";

type Props = { assetId: string; onClose: () => void };

const sections = ["overview", "metadata", "projection", "history", "jobs"] as const;

export function AssetDetailsPanel({ assetId, onClose }: Props) {
  const [data, setData] = useState<AssetDetails | null>(null);
  const [section, setSection] = useState<(typeof sections)[number]>("overview");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [forceAnalysis, setForceAnalysis] = useState(false);

  async function load(signal?: AbortSignal) {
    setError("");
    const response = await fetch("/api/v1/assets/" + encodeURIComponent(assetId), { signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(payload.detail || "Unable to load asset details");
    setData(payload);
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).catch(reason => !controller.signal.aborted && setError(reason.message));
    return () => controller.abort();
  }, [assetId]);

  async function action(name: string, extra: Record<string, unknown> = {}) {
    if (name === "reanalyze" && extra.force && !window.confirm("Force a new paid AI analysis for this asset?")) return;
    setBusy(name); setNotice("");
    try {
      const response = await fetch("/api/v1/admin/assets/" + encodeURIComponent(assetId) + "/actions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: name, ...extra, confirmed: Boolean(extra.force) }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(payload.detail || "Operation was rejected");
      setNotice(name === "cancel_job" ? "Queued job cancelled." : "Operation accepted. It will run asynchronously.");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Operation failed");
    } finally { setBusy(""); }
  }

  const sourceProvider = data?.sources.some(source => String(source.source_type || "").includes("sharepoint")) ? "sharepoint" : "google-drive";
  return <><aside className="asset-details" aria-label="Asset details">
    <header><div><small>Asset details</small><h2>{data?.sources[0]?.filename as string || assetId}</h2></div><button onClick={onClose} aria-label="Close asset details">×</button></header>
    <nav>{sections.map(name => <button key={name} className={section === name ? "active" : ""} onClick={() => setSection(name)}>{name}</button>)}</nav>
    {error && <div className="panel-error" role="alert">{error}</div>}
    {notice && <div className="panel-notice" role="status">{notice}</div>}
    {!data && !error && <div className="panel-loading">Loading asset details&</div>}
    {data && <div className="asset-details-body">
      <div className="lifecycle"><b>{data.lifecycle_status.replaceAll("_", " ")}</b><span>PostgreSQL record</span></div>
      {section === "overview" && <>
        <Detail title="Identity" value={data.asset} />
        <Detail title="Source records" value={data.sources} />
        <Detail title="Managed storage and verification" value={data.storage} />
      </>}
      {section === "metadata" && <>
        <Summary analysis={data.active_analysis} />
        <SafeJsonTree value={data.active_analysis?.metadata_json} maxDepth={data.limits.max_json_depth} maxNodes={data.limits.max_json_nodes} />
      </>}
      {section === "projection" && <>
        <p><b>Projection version:</b> {data.active_analysis?.search_projection_version || "Not built"}</p>
        <SafeJsonTree value={data.active_analysis?.search_projection} maxDepth={data.limits.max_json_depth} maxNodes={data.limits.max_json_nodes} />
      </>}
      {section === "history" && <><p>{data.analysis_total} analysis attempt(s)</p><div className="analysis-history">{data.analysis_history.map(item => <AnalysisHistoryCard key={String(item.id)} analysis={item} showCost={data.can_administer} />)}</div></>}
      {section === "jobs" && <><p>{data.job_total} related job(s)</p>{data.pipelines.map(item => <Detail key={String(item.id)} title={"Pipeline · " + String(item.state)} value={item} />)}{data.jobs.map(job => <div className="job-row" key={String(job.id)}><div><b>{String(job.job_type)}</b><small>{String(job.status)} · {String(job.attempt_count)}/{String(job.max_attempts)}</small></div>{job.cancelable && data.can_administer && <button disabled={busy === "cancel_job"} onClick={() => action("cancel_job", { job_id: job.id })}>Cancel</button>}</div>)}</>}
      {data.can_administer && <div className="asset-operations">
        <b>Operator actions</b>
        <button disabled={Boolean(busy)} onClick={() => { setForceAnalysis(false); setAnalysisOpen(true); }}>Analyze metadata</button>
        <button disabled={Boolean(busy)} onClick={() => { setForceAnalysis(true); setAnalysisOpen(true); }}>Force reanalysis</button>
        <button disabled={Boolean(busy)} onClick={() => action("rebuild_projection")}>Rebuild projection</button>
        <button disabled={Boolean(busy)} onClick={() => action("reindex")}>Reindex</button>
        <button disabled={Boolean(busy)} onClick={() => action("retry_failed_stage")}>Retry failed stage</button>
      </div>}
    </div>}
  </aside>{data && <AnalyzeMetadataDialog
    open={analysisOpen}
    assetIds={[assetId]}
    sourceProvider={sourceProvider}
    authorized={data.can_administer}
    defaultProfile={String(data.active_analysis?.metadata_profile || "")}
    defaultProfileVersion={data.active_analysis?.metadata_profile_version ? String(data.active_analysis.metadata_profile_version) : null}
    forceInitially={forceAnalysis}
    includeProviderBatchId={data.can_administer}
    onClose={() => setAnalysisOpen(false)}
    onSubmitted={() => void load().catch(reason => setError(reason.message))}
  />}</>;
}

function Detail({ title, value }: { title: string; value: unknown }) {
  return <details className="detail-card"><summary>{title}</summary><SafeJsonTree value={value} maxDepth={6} maxNodes={400} /></details>;
}

function Summary({ analysis }: { analysis: Record<string, any> | null }) {
  if (!analysis) return <p className="muted">No AI analysis is available.</p>;
  return <div className="analysis-summary"><p><b>{analysis.metadata_profile}</b> v{analysis.metadata_profile_version}</p><p>{analysis.ai_provider || "provider"} / {analysis.ai_model || "model pending"}</p><p>Prompt {analysis.prompt_version} · Pipeline {analysis.pipeline_version}</p><p>Status: {analysis.status}{analysis.processing_stage ? " · " + analysis.processing_stage : ""}</p></div>;
}
