import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Asset, AssetDetails, AssetMetadata } from "../types";
import type { VideoSearchItem, VideoSearchMatch } from "../hooks/useVideoSearch";
import { AnalyzeMetadataDialog } from "./AnalyzeMetadataDialog";
import { SquareImageGenerationDialog } from "./SquareImageGenerationDialog";
import { AnalysisHistoryCard } from "./AnalysisHistoryCard";
import { AssetStatusBadge } from "./AssetStatusBadge";
import { SafeJsonTree } from "./SafeJsonTree";
import { fileTypeGlyph, fileTypeLabel, fileTypeLogo, fileTypeTone, getFileType, isAvifAsset, isPreviewableAsset, isTextAsset } from "../utils/fileType";
import { assetPreviewUrl, explorerAssetUrl } from "../utils/mediaUrls";
import { readTextPreview, TEXT_PREVIEW_RANGE } from "../utils/textPreview";
import googleDriveLogoUrl from "../../assets/logos/google-drive-logo.svg";
import geminiSparkleUrl from "../../assets/gemini-sparkle.svg";
import seedanceLogoUrl from "../../assets/logos/seedance-logo.svg";
import { LocationBreadcrumb, itemLocationBreadcrumb } from "./LocationBreadcrumb";

type Props = {
  item: Asset | null;
  assetId?: string | null;
  metadata?: AssetMetadata;
  videoAnalysis?: VideoSearchItem | null;
  onClose: () => void;
  onPreview?: (item: Asset) => void;
  onDelete?: () => void;
  onMove?: () => void;
  onOpenFolder?: (id: string, ancestors: Array<{ id: string; name: string }>) => void;
  canManageContent?: boolean;
  onOpenGeneratedAsset?: (assetId: string) => void;
};

type Section = "details" | "activity" | "analysis" | "prompts" | "metadata" | "history" | "jobs";

type ActivityTone = "success" | "warning" | "danger" | "neutral";
type ActivityEntry = { id: string; title: string; detail: string; category: string; tone: ActivityTone; at?: string };

export function AssetDetailsPanel({ item, assetId, metadata, videoAnalysis, onClose, onPreview, onDelete, onMove, onOpenFolder, canManageContent = false, onOpenGeneratedAsset }: Props) {
  const [data, setData] = useState<AssetDetails | null>(null);
  const [section, setSection] = useState<Section>("details");
  const [coreDetailsError, setCoreDetailsError] = useState("");
  const [locationError, setLocationError] = useState("");
  const [resolutionError, setResolutionError] = useState("");
  const [loading, setLoading] = useState(Boolean(assetId));
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [generationOpen, setGenerationOpen] = useState(false);
  const [forceAnalysis, setForceAnalysis] = useState(false);
  const [locationNodes, setLocationNodes] = useState<Array<{ id: string; name: string }>>([]);
  const [locationStatus, setLocationStatus] = useState<"available" | "unavailable" | null>(null);
  const [locationLoading, setLocationLoading] = useState(Boolean(item?.id));
  const [loadedVideoAnalysis, setLoadedVideoAnalysis] = useState<VideoSearchItem | null>(null);
  const [videoDetailsLoading, setVideoDetailsLoading] = useState(false);
  const [videoDetailsError, setVideoDetailsError] = useState("");

  async function load(signal?: AbortSignal) {
    if (!assetId) { setData(null); setLoading(false); return; }
    setCoreDetailsError(""); setResolutionError(""); setLoading(true);
    const params = new URLSearchParams();
    if (item?.external_source_id) params.set("external_source_id", item.external_source_id);
    const response = await fetch("/api/v1/assets/" + encodeURIComponent(assetId) + (params.toString() ? "?" + params : ""), { signal });
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
      if (!controller.signal.aborted) { setLoading(false); setCoreDetailsError(reason.message); }
    });
    return () => controller.abort();
  }, [assetId, item?.id, item?.external_source_id]);

  useEffect(() => {
    setLoadedVideoAnalysis(null);
    setVideoDetailsError("");
    if (videoAnalysis || item?.kind !== "video" || !item.source_asset_id) {
      setVideoDetailsLoading(false);
      return;
    }
    const controller = new AbortController();
    setVideoDetailsLoading(true);
    const params = new URLSearchParams();
    if (item.external_source_id) params.set("external_source_id", item.external_source_id);
    void fetch("/api/v1/search/video/" + encodeURIComponent(item.source_asset_id) + (params.size ? "?" + params : ""), {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    }).then(async response => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
        throw Error(detail || "Unable to load video processing details");
      }
      if (!controller.signal.aborted) setLoadedVideoAnalysis(payload as VideoSearchItem);
    }).catch(reason => {
      if (!controller.signal.aborted) setVideoDetailsError(reason instanceof Error ? reason.message : "Unable to load video processing details");
    }).finally(() => {
      if (!controller.signal.aborted) setVideoDetailsLoading(false);
    });
    return () => controller.abort();
  }, [item?.kind, item?.source_asset_id, videoAnalysis]);

  useEffect(() => {
    if (!item?.id) { setLocationNodes([]); setLocationStatus(null); setLocationLoading(false); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({ provider: item.provider });
    if (item.external_source_id) params.set("external_source_id", item.external_source_id);
    setLocationLoading(true); setLocationStatus(null); setLocationError("");
    void fetch("/api/explorer/items/" + encodeURIComponent(item.id) + "/location?" + params.toString(), { signal: controller.signal })
      .then(async response => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw Error(payload.detail || "Unable to resolve asset location");
        setLocationNodes(Array.isArray(payload.breadcrumb) ? payload.breadcrumb : []);
        setLocationStatus(payload.status === "available" ? "available" : "unavailable");
      })
      .catch(() => { if (!controller.signal.aborted) { setLocationNodes([]); setLocationStatus("unavailable"); setLocationError("Unable to resolve asset location"); } })
      .finally(() => { if (!controller.signal.aborted) setLocationLoading(false); });
    return () => controller.abort();
  }, [item?.id, item?.provider, item?.external_source_id]);

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
      setCoreDetailsError("");
      await load();
    } catch (reason) {
      setCoreDetailsError(reason instanceof Error ? reason.message : "Operation failed");
    } finally { setBusy(""); }
  }

  const source = data?.sources[0] || {};
  const assetRecord = data?.asset || {};
  const displayName = item?.name || stringValue(source.filename) || stringValue(assetRecord.filename) || (loading ? "Loading..." : "Select a file");
  const provider = item?.provider || (String(source.source_type || "").includes("sharepoint") ? "sharepoint" : "google-drive");
  const sourceProvider = provider === "sharepoint" ? "sharepoint" : "google-drive";
  const kind = item?.kind === "folder" ? "folder" : inferKind(item?.mime_type || stringValue(source.mime_type) || stringValue(assetRecord.mime_type), displayName);
  const fileType = getFileType(item?.mime_type || stringValue(source.mime_type) || stringValue(assetRecord.mime_type), kind);
  const effectiveVideoAnalysis = videoAnalysis || loadedVideoAnalysis;
  const hasVideoAnalysis = Boolean(effectiveVideoAnalysis?.analysis_run_id);
  const tabs: Section[] = data
    ? ["details", "activity", ...(hasVideoAnalysis ? ["analysis" as const, "prompts" as const] : []), "metadata", "history", "jobs"]
    : hasVideoAnalysis
      ? ["details", "activity", "analysis", "prompts"]
      : ["details", "activity"];
  const activity = useMemo(() => buildActivity(item, data, effectiveVideoAnalysis), [item, data, effectiveVideoAnalysis]);

  return <><aside className="asset-details asset-inspector" aria-label="File information">
    <header className="asset-inspector-header">
      <span className={"asset-kind-mark " + kind + " " + fileTypeTone(fileType)} aria-hidden="true">{fileTypeLogo(fileType) ? <img className="google-workspace-file-logo" src={fileTypeLogo(fileType)!} alt="" /> : fileTypeGlyph(fileType)}</span>
      <div><small>{provider === "sharepoint" ? "SharePoint" : "Google Drive"}</small><h2 title={displayName}>{displayName}</h2></div>
      <button onClick={onClose} aria-label="Close file information" title="Close">×</button>
    </header>
    <nav aria-label="File information sections">{tabs.map(name => <button key={name} className={section === name ? "active" : ""} aria-current={section === name ? "page" : undefined} onClick={() => setSection(name)}>{name === "analysis" ? "AI analysis" : name === "prompts" ? "Prompts" : name}</button>)}</nav>
    {coreDetailsError && !data && !item && <div className="panel-error" role="alert">{coreDetailsError}</div>}
    {notice && <div className="panel-notice" role="status">{notice}</div>}
    {loading && <div className="panel-loading" role="status" aria-label="Loading file information"><span className="panel-loading-spinner" aria-hidden="true" /></div>}
    {!loading && !item && !data && <InspectorEmpty />}
    {!loading && (item || data) && <div className="asset-details-body">
      {videoDetailsLoading && <div className="panel-notice" role="status">Loading video activity and AI analysis…</div>}
      {videoDetailsError && <div className="panel-error" role="alert">{videoDetailsError}</div>}
      {section === "details" && <FriendlyDetails item={item} data={data} metadata={metadata} provider={provider} onPreview={onPreview} onOpenFolder={onOpenFolder} locationNodes={locationNodes} locationStatus={locationStatus} locationLoading={locationLoading} canManageContent={canManageContent} />}
      {section === "activity" && <Activity entries={activity} />}
      {section === "analysis" && effectiveVideoAnalysis && <VideoAnalysisDetails analysis={effectiveVideoAnalysis} />}
      {section === "prompts" && effectiveVideoAnalysis && <VideoGenerationPrompts analysis={effectiveVideoAnalysis} />}
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
      {(data?.can_administer || data?.can_generate) && <div className="asset-operations" aria-label="Asset actions">
        <div className="asset-action-toolbar">
        {data?.can_generate && kind === "image" && assetId && <button className="asset-action-primary" disabled={Boolean(busy)} onClick={() => setGenerationOpen(true)}><AssetActionIcon name="generate" /><span>Generate square</span></button>}
        {data?.can_administer && <button className={data?.can_generate && kind === "image" ? "" : "asset-action-primary"} disabled={Boolean(busy)} onClick={() => { setForceAnalysis(false); setAnalysisOpen(true); }}><AssetActionIcon name="analyze" /><span>Analyze metadata</span></button>}
        {data?.can_administer && onMove && <button disabled={Boolean(busy)} onClick={onMove}><AssetActionIcon name="move" /><span>Move</span></button>}
        {data?.can_administer && onDelete && <button className="asset-action-danger" disabled={Boolean(busy)} onClick={onDelete}><AssetActionIcon name="delete" /><span>Delete</span></button>}
        {data?.can_administer && <details className="asset-action-menu">
          <summary><AssetActionIcon name="more" /><span>More actions</span></summary>
          <div>
            <button disabled={Boolean(busy)} onClick={() => { setForceAnalysis(true); setAnalysisOpen(true); }}><AssetActionIcon name="analyze" /><span>Force reanalysis</span></button>
            <button disabled={Boolean(busy)} onClick={() => action("rebuild_projection")}><AssetActionIcon name="rebuild" /><span>Rebuild projection</span></button>
            <button disabled={Boolean(busy)} onClick={() => action("reindex")}><AssetActionIcon name="index" /><span>Reindex asset</span></button>
            <button disabled={Boolean(busy)} onClick={() => action("retry_failed_stage")}><AssetActionIcon name="retry" /><span>Retry failed stage</span></button>
          </div>
        </details>}
        </div>
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
    onSubmitted={() => void load().catch(reason => setCoreDetailsError(reason.message))}
  />}{data && assetId && <SquareImageGenerationDialog
    open={generationOpen}
    assetId={assetId}
    sourceName={displayName}
    sourcePreviewUrl={resolvePreviewUrl(item, source)}
    onClose={() => setGenerationOpen(false)}
    onOpenAsset={onOpenGeneratedAsset}
  />}</>;
}

type AssetActionIconName = "generate" | "analyze" | "move" | "delete" | "more" | "rebuild" | "index" | "retry";

export function AssetActionIcon({ name }: { name: AssetActionIconName }) {
  const paths: Record<AssetActionIconName, string> = {
    generate: "M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z M5 17v4M3 19h4",
    analyze: "M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8L18 15z",
    move: "M5 9l-3 3 3 3M2 12h14M12 5l3-3 3 3M15 2v14",
    delete: "M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5",
    more: "M5 12h.01M12 12h.01M19 12h.01",
    rebuild: "M19 8a7 7 0 10.5 8M19 3v5h-5",
    index: "M4 6l8-3 8 3-8 3-8-3zM4 12l8 3 8-3M4 17l8 3 8-3",
    retry: "M20 7v5h-5M19 12a7 7 0 11-2-5",
  };
  return <svg className="asset-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function FriendlyDetails({ item, data, metadata, provider, onPreview, onOpenFolder, locationNodes, locationStatus, locationLoading, canManageContent }: { item: Asset | null; data: AssetDetails | null; metadata?: AssetMetadata; provider: Asset["provider"]; onPreview?: (item: Asset) => void; onOpenFolder?: (id: string, ancestors: Array<{ id: string; name: string }>) => void; locationNodes: Array<{ id: string; name: string }>; locationStatus: "available" | "unavailable" | null; locationLoading: boolean; canManageContent: boolean }) {
  const source = data?.sources[0] || {};
  const assetRecord = data?.asset || {};
  const inferredKind = inferKind(item?.mime_type || stringValue(source.mime_type) || stringValue(assetRecord.mime_type), item?.name || stringValue(source.filename));
  const kind = inferredKind === "other" && item?.kind ? item.kind : inferredKind;
  const declaredMime = item?.mime_type || stringValue(source.mime_type) || stringValue(assetRecord.mime_type);
  const fileType = getFileType(declaredMime, kind, item?.name || stringValue(source.filename));
  const mimeType = (declaredMime && declaredMime !== "application/octet-stream" ? declaredMime : inferImageMime(item?.name || stringValue(source.filename)) || declaredMime) || "Unknown";
  const size = item?.size ?? numberValue(source.size_bytes) ?? numberValue(assetRecord.size_bytes);
  const modified = item?.modified_at || stringValue(source.source_modified_at) || stringValue(assetRecord.updated_at);
  const created = stringValue(source.source_created_at) || stringValue(assetRecord.created_at);
  const location = resolveLocation(item, source);
  const breadcrumb = locationStatus === "available" ? locationNodes : (data?.location_breadcrumb?.length ? data.location_breadcrumb : itemLocationBreadcrumb(item));
  const displayBreadcrumb = breadcrumb.length ? breadcrumb : (location ? location.split(/\s*[/\\]\s*/).map((name, index) => ({ id: "location-" + index, name })) : []);
  const locationUnavailable = locationStatus === "unavailable" || (locationStatus === null && data?.location_status === "unavailable");
  const webUrl = resolveProviderWebUrl(item, source, provider);
  const previewUrl = resolvePreviewUrl(item, source);
  const [previewFailed, setPreviewFailed] = useState(false);
  useEffect(() => setPreviewFailed(false), [previewUrl]);
  const previewName = item?.name || stringValue(source.filename) || "asset";

  return <>
    <div className={"inspector-preview" + (item && isTextAsset(item) ? " inspector-preview--text" : "")}>
      {item && isTextAsset(item) ? <TextInspectorPreview item={item} canEdit={data?.can_manage_content ?? canManageContent} /> : previewUrl && (kind === "image" || kind === "video") && !previewFailed ? <>
        <img src={previewUrl} alt={"Preview of " + previewName} referrerPolicy="no-referrer" onError={() => setPreviewFailed(true)} />
        {kind === "video" && <span className="inspector-play" aria-hidden="true">▶</span>}
      </> : <span className={"asset-kind-mark large " + kind + " " + fileTypeTone(fileType)}>{fileTypeLogo(fileType) ? <img className="google-workspace-file-logo" src={fileTypeLogo(fileType)!} alt="" /> : fileTypeGlyph(fileType)}</span>}
      <div className="inspector-preview-actions">
        {item && onPreview && isPreviewableAsset(item) && <button type="button" onClick={() => onPreview(item)}>Open preview</button>}
        {webUrl && <a className={"open-provider-link" + (provider === "google-drive" ? " open-provider-link--drive" : "")} href={webUrl} target="_blank" rel="noopener noreferrer">
          {provider === "google-drive" && <img src={googleDriveLogoUrl} alt="" aria-hidden="true" />}
          <span>Open in {provider === "sharepoint" ? "SharePoint" : "Google Drive"}</span>
        </a>}
      </div>
    </div>

    <section className="inspector-section" aria-labelledby="file-properties-heading">
      <h3 id="file-properties-heading">File details</h3>
      <dl className="inspector-properties">
        <Info label="Type" value={`${fileTypeLabel(fileType)} · ${mimeType}`} />
        <Info label="Size" value={formatBytes(size)} />
        <Info className="inspector-location" label="Location" value={locationLoading ? <span className="location-loading" aria-busy="true">Resolving location...</span> : <LocationBreadcrumb nodes={displayBreadcrumb} unavailable={locationStatus !== "available"} onOpenFolder={breadcrumb.length ? onOpenFolder : undefined} />} />
        {kind === "image" && <Info label="Resolution" value={formatResolution(data?.image_width ?? item?.image_width, data?.image_height ?? item?.image_height)} />}
        <Info label="Provider" value={provider === "sharepoint" ? "Microsoft SharePoint" : "Google Drive"} />
        <Info label="Modified" value={humanDate(modified)} />
        {created && <Info label="Created" value={humanDate(created)} />}
      </dl>
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

function TextInspectorPreview({ item, canEdit }: { item: Asset; canEdit: boolean }) {
  const [state, setState] = useState<{ text: string; truncated: boolean; error: boolean } | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    setEditing(false);
    setSaveError(null);
    void fetch(explorerAssetUrl(item, "media"), {
      signal: controller.signal,
      credentials: "same-origin",
      headers: { Range: TEXT_PREVIEW_RANGE },
    })
      .then(response => readTextPreview(response, controller.signal))
      .then(result => { if (!controller.signal.aborted) setState({ ...result, error: false }); })
      .catch(() => { if (!controller.signal.aborted) setState({ text: "", truncated: false, error: true }); });
    return () => controller.abort();
  }, [item.id, item.provider, item.external_source_id]);

  async function save() {
    if (!state || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const query = new URLSearchParams({ provider: item.provider });
      if (item.external_source_id) query.set("external_source_id", item.external_source_id);
      const response = await fetch("/api/explorer/items/" + encodeURIComponent(item.id) + "/content?" + query, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "text/plain" },
        body: draft,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(typeof payload.detail === "string" ? payload.detail : "Unable to save text file.");
      setState({ ...state, text: draft, truncated: false });
      setEditing(false);
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Unable to save this text file. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (!state) return <div className="inspector-text-preview loading" role="status">Loading text preview…</div>;
  if (state.error) return <div className="inspector-text-preview error">Text preview unavailable.</div>;
  return <div className="inspector-text-preview">
    {editing ? <>
      <textarea aria-label="Text file content" value={draft} onChange={event => setDraft(event.target.value)} />
      {saveError && <p className="inspector-text-save-error" role="alert">{saveError}</p>}
      <div className="inspector-text-actions">
        <button type="button" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button>
        <button type="button" disabled={saving} onClick={() => { setEditing(false); setSaveError(null); }}>Cancel</button>
      </div>
    </> : <>
      <pre>{state.text || "This text file is empty."}</pre>
      {canEdit && <div className="inspector-text-actions">
        <button type="button" onClick={() => { setDraft(state.text); setSaveError(null); setEditing(true); }}>Edit text</button>
      </div>}
      {state.truncated && <small>Showing the first 1 MB. Open the full preview to read the rest.</small>}
    </>}
  </div>;
}

function Activity({ entries }: { entries: ActivityEntry[] }) {
  if (!entries.length) return <div className="inspector-empty compact"><b>No activity yet</b><span>Processing updates and file changes will appear here.</span></div>;
  return <section className="activity-timeline" aria-label="Asset activity timeline">
    <div className="activity-timeline-heading"><div><h3>Processing timeline</h3><p>Newest activity first. Each entry explains what happened to this file.</p></div><span>{entries.length} event{entries.length === 1 ? "" : "s"}</span></div>
    <ol className="activity-list">{entries.map(entry => <li key={entry.id} data-tone={entry.tone}><i aria-hidden="true" /><div><div className="activity-entry-heading"><b>{entry.title}</b><span className="activity-category">{entry.category}</span></div><span className="activity-detail">{entry.detail}</span>{entry.at && <time dateTime={entry.at}>{humanDate(entry.at)}</time>}</div></li>)}</ol>
  </section>;
}
function InspectorEmpty() {
  return <div className="inspector-empty"><span className="info-empty-icon" aria-hidden="true">i</span><b>Select a file or folder</b><span>Its preview, location, metadata and activity will appear here.</span></div>;
}

function formatResolution(width?: number | null, height?: number | null): string {
  return width && height ? `${width} \u00d7 ${height} px` : "Resolution unavailable";
}

function Info({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return <div className={className}><dt>{label}</dt><dd>{value}</dd></div>;
}

function Detail({ title, value }: { title: string; value: unknown }) {
  return <details className="detail-card"><summary>{title}</summary><SafeJsonTree value={value} maxDepth={6} maxNodes={400} /></details>;
}

function Summary({ analysis }: { analysis: Record<string, any> | null }) {
  if (!analysis) return <p className="muted">No AI analysis is available.</p>;
  return <div className="analysis-summary"><p><b>{analysis.metadata_profile}</b> v{analysis.metadata_profile_version}</p><p>{analysis.ai_provider || "provider"} / {analysis.ai_model || "model pending"}</p><p>Prompt {analysis.prompt_version} · Pipeline {analysis.pipeline_version}</p><p>Status: {analysis.status}{analysis.processing_stage ? " · " + analysis.processing_stage : ""}</p></div>;
}

export function buildActivity(item: Asset | null, data: AssetDetails | null, videoAnalysis?: VideoSearchItem | null): ActivityEntry[] {
  const entries: ActivityEntry[] = [];
  if (item?.modified_at) entries.push({
    id: "modified", title: "File updated", category: "Source file", tone: "neutral",
    detail: `The file was updated in ${item.provider === "sharepoint" ? "SharePoint" : "Google Drive"}.`, at: item.modified_at,
  });
  data?.analysis_history.forEach((analysis, index) => {
    const status = String(analysis.status || "updated");
    const completed = status === "completed";
    entries.push({
      id: `analysis-${String(analysis.id || index)}`,
      title: completed ? "AI metadata analysis completed" : `AI metadata analysis ${readableStatus(status)}`,
      category: "AI analysis", tone: completed ? "success" : status === "failed" ? "danger" : "warning",
      detail: completed
        ? `Metadata was generated with ${String(analysis.ai_provider || "AI")} using ${String(analysis.ai_model || "the selected model")}.`
        : `Metadata analysis is ${readableStatus(status)} using ${String(analysis.ai_provider || "AI")}.`,
      at: stringValue(analysis.completed_at) || stringValue(analysis.updated_at) || stringValue(analysis.created_at),
    });
  });
  data?.jobs.slice(0, 20).forEach((job, index) => entries.push(describeJobActivity(job, index)));
  if (videoAnalysis?.steps?.length) {
    videoAnalysis.steps.forEach((step, index) => {
      const status = String(step.status || "not_started");
      const completed = status === "completed";
      const failed = status === "failed";
      const attempts = step.max_attempts > 0 ? ` Attempt ${step.attempt_count}/${step.max_attempts}.` : "";
      const error = step.error_code ? ` Error: ${humanizeError(step.error_code)}.` : "";
      entries.push({
        id: `video-step-${step.key}-${index}`,
        title: `${step.label} ${completed ? "completed" : failed ? "failed" : readableStatus(status)}`,
        category: "Video pipeline", tone: completed ? "success" : failed ? "danger" : status === "not_started" ? "neutral" : "warning",
        detail: `${step.label} is ${readableStatus(status)}.${attempts}${error}`,
        at: step.updated_at || undefined,
      });
    });
  } else if (videoAnalysis?.analysis_run_id) entries.push({
    id: `video-analysis-${videoAnalysis.analysis_run_id}`,
    title: "Video AI analysis available",
    category: "AI analysis",
    tone: "success",
    detail: `${videoAnalysis.matches.length} matching segment${videoAnalysis.matches.length === 1 ? "" : "s"} are available. Best match starts at ${formatVideoTimestamp(videoAnalysis.best_match.start_ms)}.`,
  });
  return entries.sort((left, right) => Date.parse(right.at || "") - Date.parse(left.at || ""));
}

export function VideoAnalysisDetails({ analysis }: { analysis: VideoSearchItem }) {
  return <section className="video-analysis-details" aria-label="Video AI analysis">
    <div className="video-analysis-summary">
      <small>BEST MATCH · {formatVideoTimestamp(analysis.best_match.start_ms)}</small>
      <h3>{analysis.best_match.summary || "Analyzed video segment"}</h3>
      {analysis.best_match.visual_description && <p>{analysis.best_match.visual_description}</p>}
      {analysis.best_match.speech && <p><b>Speech:</b> {analysis.best_match.speech}</p>}
    </div>
    <div className="video-analysis-heading">
      <h3>Matching segments</h3>
      <span>{analysis.matches.length}</span>
    </div>
    <ol className="video-analysis-segments">{analysis.matches.map((match, index) => <li key={`${match.start_ms}-${match.end_ms}-${index}`}>
      <div><b>{formatVideoTimestamp(match.start_ms)}–{formatVideoTimestamp(match.end_ms)}</b><small>{formatConfidence(match)}</small></div>
      <strong>{match.summary || "Analyzed segment"}</strong>
      {match.visual_description && <p>{match.visual_description}</p>}
      {match.speech && <p><b>Speech:</b> {match.speech}</p>}
    </li>)}</ol>
  </section>;
}

type VideoPromptProvider = "seedance" | "gemini";

type VideoPromptSuggestion = {
  provider: VideoPromptProvider;
  label: string;
  model: string;
  description: string;
  prompt: string;
};

export function buildVideoGenerationPrompts(analysis: VideoSearchItem): VideoPromptSuggestion[] {
  const matches = [...analysis.matches]
    .filter(match => Number.isFinite(match.start_ms) && Number.isFinite(match.end_ms))
    .sort((left, right) => left.start_ms - right.start_ms)
    .slice(0, 8);
  const scenes = matches.length ? matches : [analysis.best_match];
  const detectedDuration = Math.max(analysis.duration_ms || 0, ...scenes.map(scene => scene.end_ms));
  const duration = detectedDuration > 0
    ? `${Math.max(1, Math.ceil(detectedDuration / 1_000))} seconds`
    : "the natural duration required by the shot sequence";
  const timeline = scenes.map((scene, index) => {
    const visual = cleanPromptText(scene.visual_description || scene.summary || "Continue the established action.");
    const summary = cleanPromptText(scene.summary);
    const speech = cleanPromptText(scene.speech);
    return `${index + 1}. ${formatVideoTimestamp(scene.start_ms)}-${formatVideoTimestamp(scene.end_ms)}: ${summary ? summary + " " : ""}${visual}${speech ? ` Spoken audio: "${speech}".` : ""}`;
  }).join("\n");
  const continuity = cleanPromptText(analysis.best_match.visual_description || analysis.best_match.summary);
  const evidenceNote = analysis.matches.length > scenes.length
    ? ` Use these ${scenes.length} representative scenes as the visual backbone and maintain coherent transitions between them.`
    : "";

  const seedance = `Create a polished, realistic video with a target duration of ${duration}.

Core visual direction:
${continuity || "Recreate the analyzed subject, setting, and action faithfully."}

Shot sequence:
${timeline || "1. Recreate the analyzed video as one continuous, natural shot."}

Direction: preserve the same subject, wardrobe, objects, environment, lighting, and spatial continuity from shot to shot. Use deliberate camera movement, physically believable motion, stable anatomy, natural timing, and clean transitions.${evidenceNote} Preserve visible words exactly when legible. Keep any quoted speech verbatim and naturally synchronized.

Avoid: invented logos or text, subtitles, watermarks, flicker, jump cuts, duplicated objects, warped hands, inconsistent clothing, or unexplained scene changes.`;

  const gemini = `Generate a coherent video from the following production brief.

TARGET
- Duration: ${duration}
- Look: realistic, detailed, production-ready
- Continuity: one consistent subject, wardrobe, environment, lighting, and object layout

SCENE PLAN
${timeline || "1. Recreate the analyzed action as a continuous scene."}

GLOBAL VISUAL REFERENCE
${continuity || "Follow the analyzed visual evidence faithfully."}

MOTION AND CAMERA
Use smooth, intentional framing and physically plausible movement. Let each action finish before the next begins. Maintain temporal continuity and consistent scale, anatomy, materials, and lighting.

AUDIO AND TEXT
Keep quoted dialogue verbatim when present and synchronize it naturally. Do not add narration, music, captions, logos, or written text unless explicitly described. Reproduce legible source text exactly; omit unclear text rather than guessing.

NEGATIVE CONSTRAINTS
No flicker, morphing, duplicate limbs or objects, warped hands, identity drift, wardrobe changes, random cuts, watermarks, or invented details.`;

  return [
    { provider: "seedance", label: "Seedance 2.5", model: "Seedance 2.5", description: "Cinematic prompt with shot flow, motion, and visual continuity.", prompt: seedance },
    { provider: "gemini", label: "Gemini Omni", model: "Gemini Omni", description: "Structured production brief with explicit scene and safety constraints.", prompt: gemini },
  ];
}

export function VideoGenerationPrompts({ analysis }: { analysis: VideoSearchItem }) {
  const suggestions = useMemo(() => buildVideoGenerationPrompts(analysis), [analysis]);
  const [provider, setProvider] = useState<VideoPromptProvider>("seedance");
  const [copyStatus, setCopyStatus] = useState("");
  const selected = suggestions.find(suggestion => suggestion.provider === provider) || suggestions[0];

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(selected.prompt);
      setCopyStatus("Copied");
    } catch {
      setCopyStatus("Copy failed");
    }
  }

  return <section className="video-prompt-panel" aria-label="Suggested video generation prompts">
    <header>
      <div><small>VIDEO GENERATION</small><h3>Recreate this video</h3><p>Suggested from {Math.min(analysis.matches.length || 1, 8)} analyzed scene{analysis.matches.length === 1 ? "" : "s"}.</p></div>
      <span>AI draft</span>
    </header>
    <div className="video-prompt-providers" role="tablist" aria-label="Video generation provider">
      {suggestions.map(suggestion => <button key={suggestion.provider} type="button" role="tab" aria-selected={provider === suggestion.provider} className={provider === suggestion.provider ? "active" : ""} onClick={() => { setProvider(suggestion.provider); setCopyStatus(""); }}>
        <i aria-hidden="true" className={"video-prompt-provider-logo " + suggestion.provider}><img src={suggestion.provider === "seedance" ? seedanceLogoUrl : geminiSparkleUrl} alt="" /></i>
        <span><b>{suggestion.label}</b><small>{suggestion.description}</small></span>
      </button>)}
    </div>
    <div className="video-prompt-output">
      <div><span>Suggested prompt - {selected.model}</span><button type="button" onClick={() => void copyPrompt()}>{copyStatus || "Copy prompt"}</button></div>
      <pre>{selected.prompt}</pre>
    </div>
    <p className="video-prompt-note">Review names, visible text, and brand details before generating. The draft only uses evidence available in the current AI analysis.</p>
  </section>;
}

function cleanPromptText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function formatVideoTimestamp(value: number): string {
  const seconds = Math.max(0, Math.floor(value / 1_000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function formatConfidence(match: VideoSearchMatch): string {
  return Number.isFinite(match.confidence) ? `${Math.round(match.confidence * 100)}% confidence` : "";
}

function describeJobActivity(job: Record<string, any>, index: number): ActivityEntry {
  const jobType = String(job.job_type || "processing");
  const status = String(job.status || "updated");
  const completed = status === "completed";
  const failed = status === "failed";
  const definitions: Record<string, { title: string; detail: string; category: string }> = {
    source_asset_download: { title: "Source image downloaded", detail: "The original image was downloaded securely from the connected source.", category: "Import" },
    asset_store: { title: "Managed copy stored", detail: "A managed copy of the image was saved for reliable processing.", category: "Storage" },
    asset_analyze: { title: "Metadata analysis processed", detail: "AI metadata processing was queued or completed for this image.", category: "AI analysis" },
    search_projection_build: { title: "Search data prepared", detail: "Search terms and phrases were built from the completed metadata.", category: "Search" },
    asset_index: { title: "Search index updated", detail: "The asset was added to the search index and is ready to be found.", category: "Search" },
  };
  const definition = definitions[jobType] || { title: "Background processing updated", detail: "A background processing step was updated.", category: "Processing" };
  const error = job.last_error_code ? ` Search indexing could not finish (${humanizeError(String(job.last_error_code))}). Use “More actions” to retry when the service is available.` : "";
  return {
    id: `job-${String(job.id || index)}`,
    title: failed ? `${definition.title} failed` : completed ? definition.title : `${definition.title} ${readableStatus(status)}`,
    category: definition.category,
    tone: failed ? "danger" : completed ? "success" : "warning",
    detail: failed ? error || "This processing step could not finish. You can retry it from More actions." : definition.detail,
    at: stringValue(job.completed_at) || stringValue(job.updated_at) || stringValue(job.created_at),
  };
}

function readableStatus(status: string): string {
  return status.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

function humanizeError(code: string): string {
  return code.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}
function sourcePath(source: Record<string, unknown>): string | undefined {
  const metadata = source.source_metadata;
  if (metadata && typeof metadata === "object" && "path" in metadata) return stringValue((metadata as Record<string, unknown>).path);
  return undefined;
}

export function resolveLocation(item: Asset | null, source: Record<string, unknown>): string {
  const candidates = [sourcePath(source), item?.folder_path, item?.ancestor_names?.join(" / ")]
    .filter((value): value is string => Boolean(value && value.trim()))
    .map(value => value.trim())
    .filter(value => value.toLowerCase() !== "current folder");
  if (!candidates.length) return "";
  return candidates.sort((left, right) => right.split(/\s*[/\\]\s*/).length - left.split(/\s*[/\\]\s*/).length)[0];
}

export function resolvePreviewUrl(item: Asset | null, source: Record<string, unknown>): string | undefined {
  if (item && isAvifAsset(item)) return assetPreviewUrl(item);
  return item?.thumbnail_url || stringValue(source.preview_url);
}

export function resolveProviderWebUrl(item: Asset | null, source: Record<string, unknown>, provider?: string): string | undefined {
  const sourceMetadata = source.source_metadata && typeof source.source_metadata === "object" ? source.source_metadata as Record<string, unknown> : {};
  const nestedMetadata = source.metadata && typeof source.metadata === "object" ? source.metadata as Record<string, unknown> : {};
  const selectedProvider = String(provider || item?.provider || source.source_type || "").toLowerCase().replaceAll("_", "-");
  const candidates = [item?.web_url, source.web_url, sourceMetadata.web_url, sourceMetadata.webViewLink, sourceMetadata.webUrl, nestedMetadata.web_url, nestedMetadata.webViewLink, nestedMetadata.webUrl];
  for (const value of candidates) {
    if (typeof value !== "string" || !value.trim()) continue;
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) continue;
      const host = url.hostname.toLowerCase().replace(/\.$/, "");
      const google = ["google-drive", "google"].includes(selectedProvider) && ["drive.google.com", "docs.google.com"].includes(host);
      const sharepoint = ["sharepoint", "microsoft", "microsoft-sharepoint"].includes(selectedProvider) && (host.endsWith(".sharepoint.com") || host.endsWith(".sharepoint-df.com") || host === "office.com" || host.endsWith(".office.com") || host === "microsoft365.com" || host.endsWith(".microsoft365.com"));
      if (google || sharepoint) { url.hash = ""; return url.toString(); }
    } catch { /* ignore malformed provider links */ }
  }
  return undefined;
}

function stringValue(value: unknown): string | undefined { return typeof value === "string" && value ? value : undefined; }
function numberValue(value: unknown): number | undefined { return typeof value === "number" && Number.isFinite(value) ? value : undefined; }
const IMAGE_MIME_BY_EXTENSION: Record<string, string> = { avif: "image/avif", bmp: "image/bmp", gif: "image/gif", jpeg: "image/jpeg", jpg: "image/jpeg", png: "image/png", webp: "image/webp" };
function inferImageMime(filename?: string): string | undefined { const extension = filename?.split(".").pop()?.toLowerCase(); return extension ? IMAGE_MIME_BY_EXTENSION[extension] : undefined; }
export function inferKind(mimeType?: string, filename?: string): Asset["kind"] { const normalized = (mimeType || "").toLowerCase(); const extensionMime = inferImageMime(filename); if (normalized.startsWith("image/")) return "image"; if ((normalized === "" || normalized === "application/octet-stream") && extensionMime) return "image"; if (normalized.startsWith("video/")) return "video"; if (normalized === "application/pdf") return "pdf"; return normalized ? "document" : "other"; }
function kindMark(kind: Asset["kind"]): string { return ({ folder: "DIR", image: "IMG", video: "VID", pdf: "PDF", document: "DOC", other: "FILE" })[kind]; }
export function readableKind(kind: Asset["kind"]): string { return ({ folder: "Folder", image: "Image", video: "Video", pdf: "PDF document", document: "Document", other: "File" })[kind]; }
export function formatBytes(value?: number): string { if (value === undefined || value === null || value < 0) return "Not available"; if (value === 0) return "0 B"; const units = ["B", "KB", "MB", "GB", "TB"]; const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1); const amount = value / 1024 ** index; return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`; }
export function humanDate(value?: string): string { if (!value) return "Not available"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString(); }