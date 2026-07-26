import { useEffect, useMemo, useState } from "react";
import type { Asset, AssetDetails, AssetMetadata } from "../types";
import { AnalyzeMetadataDialog } from "./AnalyzeMetadataDialog";
import { AnalysisHistoryCard } from "./AnalysisHistoryCard";
import { AssetStatusBadge } from "./AssetStatusBadge";
import { SafeJsonTree } from "./SafeJsonTree";

type Props = {
  item: Asset | null;
  assetId?: string | null;
  metadata?: AssetMetadata;
  onClose: () => void;
  onPreview?: (item: Asset) => void;
};

type Section = "details" | "activity" | "metadata" | "history" | "jobs";

type ActivityEntry = { id: string; title: string; detail: string; at?: string };

export function AssetDetailsPanel({ item, assetId, metadata, onClose, onPreview }: Props) {
  const [data, setData] = useState<AssetDetails | null>(null);
  const [section, setSection] = useState<Section>("details");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(assetId));
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [forceAnalysis, setForceAnalysis] = useState(false);

  async function load(signal?: AbortSignal) {
    if (!assetId) { setData(null); setLoading(false); return; }
    setError(""); setLoading(true);
    const response = await fetch("/api/v1/assets/" + encodeURIComponent(assetId), { signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(payload.detail || "Unable to load asset details");
    setData(payload);
    setLoading(false);
  }

  useEffect(() => {
    setSection("details"); setNotice(""); setData(null); setLoading(Boolean(assetId));
    if (!assetId) return;
    const controller = new AbortController();
    void load(controller.signal).catch(reason => {
      if (!controller.signal.aborted) { setLoading(false); setError(reason.message); }
    });
    return () => controller.abort();
  }, [assetId]);

  async function action(name: string, extra: Record<string, unknown> = {}) {
    if (!assetId) return;
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

  const source = data?.sources[0] || {};
  const assetRecord = data?.asset || {};
  const displayName = item?.name || stringValue(source.filename) || stringValue(assetRecord.filename) || (loading ? "Loading..." : "Select a file");
  const provider = item?.provider || (String(source.source_type || "").includes("sharepoint") ? "sharepoint" : "google-drive");
  const sourceProvider = provider === "sharepoint" ? "sharepoint" : "google-drive";
  const kind = item?.kind || inferKind(stringValue(source.mime_type) || stringValue(assetRecord.mime_type));
  const tabs: Section[] = data ? ["details", "activity", "metadata", "history", "jobs"] : ["details", "activity"];
  const activity = useMemo(() => buildActivity(item, data), [item, data]);

  return <><aside className="asset-details asset-inspector" aria-label="File information">
    <header className="asset-inspector-header">
      <span className={"asset-kind-mark " + kind} aria-hidden="true">{kindMark(kind)}</span>
      <div><small>{provider === "sharepoint" ? "SharePoint" : "Google Drive"}</small><h2 title={displayName}>{displayName}</h2></div>
      <button onClick={onClose} aria-label="Close file information" title="Close">×</button>
    </header>
    <nav aria-label="File information sections">{tabs.map(name => <button key={name} className={section === name ? "active" : ""} aria-current={section === name ? "page" : undefined} onClick={() => setSection(name)}>{name}</button>)}</nav>
    {error && <div className="panel-error" role="alert">{error}</div>}
    {notice && <div className="panel-notice" role="status">{notice}</div>}
    {loading && <div className="panel-loading" role="status">Loading file information...</div>}
    {!loading && !item && !data && <InspectorEmpty />}
    {!loading && (item || data) && <div className="asset-details-body">
      {section === "details" && <FriendlyDetails item={item} data={data} metadata={metadata} provider={provider} onPreview={onPreview} />}
      {section === "activity" && <Activity entries={activity} />}
      {section === "metadata" && data && <>
        <Summary analysis={data.active_analysis} />
        <h3 className="panel-section-title">Metadata document</h3>
        <SafeJsonTree value={data.active_analysis?.metadata_json} maxDepth={data.limits.max_json_depth} maxNodes={data.limits.max_json_nodes} />
        <h3 className="panel-section-title">Search projection</h3>
        <p><b>Projection version:</b> {String(data.active_analysis?.search_projection_version || "Not built")}</p>
        <SafeJsonTree value={data.active_analysis?.search_projection} maxDepth={data.limits.max_json_depth} maxNodes={data.limits.max_json_nodes} />
      </>}
      {section === "history" && data && <><p>{data.analysis_total} analysis attempt(s)</p><div className="analysis-history">{data.analysis_history.map(entry => <AnalysisHistoryCard key={String(entry.id)} analysis={entry} showCost={data.can_administer} />)}</div></>}
      {section === "jobs" && data && <><p>{data.job_total} related job(s)</p>{data.pipelines.map(entry => <Detail key={String(entry.id)} title={"Pipeline · " + String(entry.state)} value={entry} />)}{data.jobs.map(job => <div className="job-row" key={String(job.id)}><div><b>{String(job.job_type)}</b><small>{String(job.status)} · {String(job.attempt_count)}/{String(job.max_attempts)}</small></div>{job.cancelable && data.can_administer && <button disabled={busy === "cancel_job"} onClick={() => action("cancel_job", { job_id: job.id })}>Cancel</button>}</div>)}</>}
      {data?.can_administer && <div className="asset-operations">
        <div className="asset-actions-label"><span>Operator tools</span><b>Asset actions</b></div>
        <button className="asset-action-primary" disabled={Boolean(busy)} onClick={() => { setForceAnalysis(false); setAnalysisOpen(true); }}>Analyze metadata</button>
        <details className="asset-action-menu">
          <summary>More actions</summary>
          <div>
            <button disabled={Boolean(busy)} onClick={() => { setForceAnalysis(true); setAnalysisOpen(true); }}>Force reanalysis</button>
            <button disabled={Boolean(busy)} onClick={() => action("rebuild_projection")}>Rebuild projection</button>
            <button disabled={Boolean(busy)} onClick={() => action("reindex")}>Reindex asset</button>
            <button disabled={Boolean(busy)} onClick={() => action("retry_failed_stage")}>Retry failed stage</button>
          </div>
        </details>
      </div>}
    </div>}
  </aside>{data && assetId && <AnalyzeMetadataDialog
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

function FriendlyDetails({ item, data, metadata, provider, onPreview }: { item: Asset | null; data: AssetDetails | null; metadata?: AssetMetadata; provider: Asset["provider"]; onPreview?: (item: Asset) => void }) {
  const source = data?.sources[0] || {};
  const assetRecord = data?.asset || {};
  const kind = item?.kind || inferKind(stringValue(source.mime_type) || stringValue(assetRecord.mime_type));
  const mimeType = item?.mime_type || stringValue(source.mime_type) || stringValue(assetRecord.mime_type) || "Unknown";
  const size = item?.size ?? numberValue(source.size_bytes) ?? numberValue(assetRecord.size_bytes);
  const modified = item?.modified_at || stringValue(source.source_modified_at) || stringValue(assetRecord.updated_at);
  const created = stringValue(source.source_created_at) || stringValue(assetRecord.created_at);
  const location = item?.folder_path || item?.ancestor_names?.join(" / ") || sourcePath(source) || "Current folder";
  const webUrl = item?.web_url || stringValue(source.web_url);
  const previewUrl = resolvePreviewUrl(item, source);
  const previewName = item?.name || stringValue(source.filename) || "asset";

  return <>
    <div className="inspector-preview">
      {previewUrl && (kind === "image" || kind === "video") ? <>
        <img src={previewUrl} alt={`Preview of ${previewName}`} referrerPolicy="no-referrer" />
        {kind === "video" && <span className="inspector-play" aria-hidden="true">▶</span>}
      </> : <span className={"asset-kind-mark large " + kind}>{kindMark(kind)}</span>}
      {item && onPreview && (kind === "image" || kind === "video") && <button type="button" onClick={() => onPreview(item)}>Open preview</button>}
    </div>

    <section className="inspector-section" aria-labelledby="file-properties-heading">
      <h3 id="file-properties-heading">File details</h3>
      <dl className="inspector-properties">
        <Info label="Type" value={`${readableKind(kind)} · ${mimeType}`} />
        <Info label="Size" value={formatBytes(size)} />
        <Info label="Location" value={location} />
        <Info label="Provider" value={provider === "sharepoint" ? "Microsoft SharePoint" : "Google Drive"} />
        <Info label="Modified" value={humanDate(modified)} />
        {created && <Info label="Created" value={humanDate(created)} />}
      </dl>
      {webUrl && <a className="open-provider-link" href={webUrl} target="_blank" rel="noreferrer">Open in {provider === "sharepoint" ? "SharePoint" : "Google Drive"}</a>}
    </section>

    <section className="inspector-section" aria-labelledby="asset-state-heading">
      <h3 id="asset-state-heading">Asset state</h3>
      <div className="inspector-state-row">
        {metadata ? <AssetStatusBadge status={metadata.processing_status} /> : data && <span className="processing-status"><i />{data.lifecycle_status.replaceAll("_", " ")}</span>}
        {metadata?.tag_ids.map(tag => <span className={"asset-status " + tag} key={tag}>{tag}</span>)}
      </div>
      <div className="inspector-rating" aria-label="Asset rating">{[1, 2, 3, 4, 5].map(star => <span className={(metadata?.rating || 0) >= star ? "filled" : ""} key={star}>★</span>)}</div>
    </section>

    {data && <section className="inspector-section">
      <h3>System information</h3>
      <Detail title="Identity" value={data.asset} />
      <Detail title="Source record" value={data.sources} />
      <Detail title="Managed storage" value={data.storage} />
    </section>}
  </>;
}

function Activity({ entries }: { entries: ActivityEntry[] }) {
  if (!entries.length) return <div className="inspector-empty"><b>No activity yet</b><span>Changes and processing events will appear here.</span></div>;
  return <ol className="activity-list">{entries.map(entry => <li key={entry.id}><i aria-hidden="true" /><div><b>{entry.title}</b><span>{entry.detail}</span>{entry.at && <time dateTime={entry.at}>{humanDate(entry.at)}</time>}</div></li>)}</ol>;
}

function InspectorEmpty() {
  return <div className="inspector-empty"><span className="info-empty-icon" aria-hidden="true">i</span><b>Select a file or folder</b><span>Its preview, location, metadata and activity will appear here.</span></div>;
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function Detail({ title, value }: { title: string; value: unknown }) {
  return <details className="detail-card"><summary>{title}</summary><SafeJsonTree value={value} maxDepth={6} maxNodes={400} /></details>;
}

function Summary({ analysis }: { analysis: Record<string, any> | null }) {
  if (!analysis) return <p className="muted">No AI analysis is available.</p>;
  return <div className="analysis-summary"><p><b>{analysis.metadata_profile}</b> v{analysis.metadata_profile_version}</p><p>{analysis.ai_provider || "provider"} / {analysis.ai_model || "model pending"}</p><p>Prompt {analysis.prompt_version} · Pipeline {analysis.pipeline_version}</p><p>Status: {analysis.status}{analysis.processing_stage ? " · " + analysis.processing_stage : ""}</p></div>;
}

function buildActivity(item: Asset | null, data: AssetDetails | null): ActivityEntry[] {
  const entries: ActivityEntry[] = [];
  if (item?.modified_at) entries.push({ id: "modified", title: "File modified", detail: `Updated in ${item.provider === "sharepoint" ? "SharePoint" : "Google Drive"}`, at: item.modified_at });
  data?.analysis_history.forEach((analysis, index) => entries.push({ id: `analysis-${String(analysis.id || index)}`, title: `Metadata analysis ${String(analysis.status || "updated")}`, detail: `${String(analysis.ai_provider || "AI")} · ${String(analysis.ai_model || "model")}`, at: stringValue(analysis.completed_at) || stringValue(analysis.updated_at) || stringValue(analysis.created_at) }));
  data?.jobs.slice(0, 20).forEach((job, index) => entries.push({ id: `job-${String(job.id || index)}`, title: `${String(job.job_type || "Processing job")} ${String(job.status || "updated")}`, detail: job.last_error_code ? String(job.last_error_code) : "Asset processing", at: stringValue(job.completed_at) || stringValue(job.updated_at) || stringValue(job.created_at) }));
  return entries.sort((left, right) => Date.parse(right.at || "") - Date.parse(left.at || ""));
}

function sourcePath(source: Record<string, unknown>): string | undefined {
  const metadata = source.source_metadata;
  if (metadata && typeof metadata === "object" && "path" in metadata) return stringValue((metadata as Record<string, unknown>).path);
  return undefined;
}

export function resolvePreviewUrl(item: Asset | null, source: Record<string, unknown>): string | undefined {
  return item?.thumbnail_url || stringValue(source.preview_url);
}

function stringValue(value: unknown): string | undefined { return typeof value === "string" && value ? value : undefined; }
function numberValue(value: unknown): number | undefined { return typeof value === "number" && Number.isFinite(value) ? value : undefined; }
function inferKind(mimeType?: string): Asset["kind"] { if (!mimeType) return "other"; if (mimeType.startsWith("image/")) return "image"; if (mimeType.startsWith("video/")) return "video"; if (mimeType === "application/pdf") return "pdf"; return "document"; }
function kindMark(kind: Asset["kind"]): string { return ({ folder: "DIR", image: "IMG", video: "VID", pdf: "PDF", document: "DOC", other: "FILE" })[kind]; }
export function readableKind(kind: Asset["kind"]): string { return ({ folder: "Folder", image: "Image", video: "Video", pdf: "PDF document", document: "Document", other: "File" })[kind]; }
export function formatBytes(value?: number): string { if (value === undefined || value === null || value < 0) return "Not available"; if (value === 0) return "0 B"; const units = ["B", "KB", "MB", "GB", "TB"]; const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1); const amount = value / 1024 ** index; return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`; }
export function humanDate(value?: string): string { if (!value) return "Not available"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString(); }
