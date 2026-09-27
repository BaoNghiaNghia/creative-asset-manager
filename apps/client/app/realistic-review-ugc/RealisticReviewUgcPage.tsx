import { useEffect, useMemo, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import { analyzeCandidate, createCampaign, importCandidate, listCampaigns, listCandidates } from "./api";
import type { Campaign, CampaignCreated, Candidate, CandidateStatus } from "./types";

const time = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";
const percent = (value: number | null) => value == null ? "—" : Math.round(value * 100) + "%";

const statusLabel: Record<CandidateStatus, string> = {
  discovered: "Discovered",
  analysis_queued: "Analysis queued",
  analyzing: "Analyzing",
  approved: "Approved",
  analysis_failed: "Analysis failed",
  rejected_no_person: "No person",
  rejected_head_ratio: "Head ratio rejected",
  rejected_expression: "Expression rejected",
  rejected_existing_headwear: "Existing headwear",
  rejected_head_occlusion: "Head occlusion",
  rejected_quality: "Quality rejected",
  rejected_ai_risk: "AI risk review",
  rejected_context: "Context rejected",
  import_queued: "Drive queued",
  importing: "Saving to Drive",
  drive_ready: "Drive ready",
  import_failed: "Drive failed",
  rejected_duplicate: "Duplicate",
};

const rejectedStatuses = new Set<CandidateStatus>([
  "rejected_no_person",
  "rejected_head_ratio",
  "rejected_expression",
  "rejected_existing_headwear",
  "rejected_head_occlusion",
  "rejected_quality",
  "rejected_ai_risk",
  "rejected_context",
]);

const analyzingStatuses = new Set<CandidateStatus>([
  "discovered",
  "analysis_queued",
  "analyzing",
]);

function candidateTone(status: CandidateStatus): string {
  if (status === "drive_ready" || status === "approved") return "positive";
  if (rejectedStatuses.has(status) || status === "analysis_failed" || status === "import_failed") return "negative";
  if (status === "rejected_duplicate") return "neutral";
  return "working";
}

export function scoutCommand(baseUrl: string, campaignId: string, token: string): string {
  const url = baseUrl.replace(/\/$/, "");
  return [
    "python apps/rrugc_scout/scout.py",
    "--base-url \"" + url + "\"",
    "--campaign-id \"" + campaignId + "\"",
    "--token \"" + token + "\"",
    "--profile-dir \"./.rrugc-pinterest-profile\"",
  ].join(" ");
}

export function RealisticReviewUgcPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [created, setCreated] = useState<CampaignCreated | null>(null);
  const [name, setName] = useState("Pinterest lifestyle references");
  const [query, setQuery] = useState("happy woman casual outdoor candid");
  const [target, setTarget] = useState(100);
  const [scrolls, setScrolls] = useState(6);
  const [autoImport, setAutoImport] = useState(true);
  const [minHeadRatio, setMinHeadRatio] = useState(20);
  const [maxHeadRatio, setMaxHeadRatio] = useState(45);
  const [minSmile, setMinSmile] = useState(65);
  const [maxOcclusion, setMaxOcclusion] = useState(25);
  const [maxAiRisk, setMaxAiRisk] = useState(20);
  const [minQuality, setMinQuality] = useState(55);
  const [minUgc, setMinUgc] = useState(55);
  const [minProductFit, setMinProductFit] = useState(55);
  const [rejectHeadwear, setRejectHeadwear] = useState(true);
  const [requireHeadVisible, setRequireHeadVisible] = useState(true);
  const [busy, setBusy] = useState(false);
  const [actionId, setActionId] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const selected = campaigns.find(item => item.id === selectedId) || null;
  const command = useMemo(
    () => created ? scoutCommand(window.location.origin, created.id, created.scout_token) : "",
    [created],
  );

  async function refreshCampaigns(signal?: AbortSignal) {
    const rows = await listCampaigns(signal);
    setCampaigns(rows);
    setSelectedId(current => current && rows.some(row => row.id === current) ? current : rows[0]?.id || "");
  }

  async function refreshCandidates(campaignId: string, signal?: AbortSignal) {
    const rows = await listCandidates(campaignId, signal);
    setCandidates(rows);
  }

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void refreshCampaigns(controller.signal).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load campaigns.");
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshCampaigns().catch(() => undefined);
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setCandidates([]);
      return;
    }
    const controller = new AbortController();
    void refreshCandidates(selectedId, controller.signal).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load candidates.");
    });
    const timer = window.setInterval(() => {
      if (!document.hidden) void refreshCandidates(selectedId).catch(() => undefined);
    }, 3500);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [selectedId]);

  async function submit() {
    if (!name.trim() || !query.trim() || busy) return;
    if (minHeadRatio >= maxHeadRatio) {
      setError("Minimum head ratio must be lower than maximum head ratio.");
      return;
    }
    setBusy(true);
    setError("");
    setCopied(false);
    try {
      const next = await createCampaign({
        name: name.trim(),
        query: query.trim(),
        target_count: target,
        max_scroll_batches: scrolls,
        auto_import: autoImport,
        min_head_ratio: minHeadRatio / 100,
        max_head_ratio: maxHeadRatio / 100,
        min_smile_score: minSmile / 100,
        max_head_occlusion: maxOcclusion / 100,
        max_ai_risk_score: maxAiRisk / 100,
        min_quality_score: minQuality / 100,
        min_ugc_score: minUgc / 100,
        min_product_fit_score: minProductFit / 100,
        require_head_visible: requireHeadVisible,
        reject_headwear: rejectHeadwear,
      });
      setCreated(next);
      await refreshCampaigns();
      setSelectedId(next.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create campaign.");
    } finally {
      setBusy(false);
    }
  }

  async function copyCommand() {
    if (!command) return;
    await navigator.clipboard.writeText(command);
    setCopied(true);
  }

  async function retryAnalysis(candidate: Candidate) {
    if (!selected || actionId) return;
    setActionId(candidate.id);
    setError("");
    try {
      const result = await analyzeCandidate(selected.id, candidate.id);
      setCandidates(rows => rows.map(row => row.id === result.candidate.id ? result.candidate : row));
      await refreshCampaigns();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue analysis.");
    } finally {
      setActionId("");
    }
  }

  async function importOne(candidate: Candidate) {
    if (!selected || actionId) return;
    setActionId(candidate.id);
    setError("");
    try {
      const result = await importCandidate(selected.id, candidate.id);
      setCandidates(rows => rows.map(row => row.id === result.candidate.id ? result.candidate : row));
      await refreshCampaigns();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue this image for Drive.");
    } finally {
      setActionId("");
    }
  }

  const kpis = {
    workflows: campaigns.length,
    running: campaigns.filter(item => item.status === "running").length,
    approved: campaigns.reduce((sum, item) => sum + item.approved, 0),
    driveReady: campaigns.reduce((sum, item) => sum + item.drive_ready, 0),
  };

  return <main className="rrugc-shell">
    <aside className="ops-sidebar">
      <div className="brand"><b><BrandIcon /></b><span><strong>Creative assets</strong><small>UGC reference automation</small></span></div>
      <WorkspaceNavigation active="realistic-review-ugc" />
    </aside>
    <section className="rrugc-main">
      <WorkspacePageHeader
        className="rrugc-header"
        route="realistic-review-ugc"
        actions={<WorkspaceBackToAssets />}
      />
      <div className="rrugc-page-body">
        {error && <div className="rrugc-error" role="alert">{error}</div>}

        <section className="rrugc-kpis" aria-label="Realistic Review UGC overview">
          <article><span>Campaigns</span><strong>{kpis.workflows}</strong></article>
          <article><span>Active</span><strong>{kpis.running}</strong></article>
          <article><span>Approved</span><strong>{kpis.approved}</strong></article>
          <article><span>Drive ready</span><strong>{kpis.driveReady}</strong></article>
        </section>

        <div className="rrugc-columns">
          <section className="rrugc-card">
            <div className="rrugc-section-heading"><div><small>NEW CAMPAIGN</small><h2>Pinterest Browser Scout</h2></div><span className="rrugc-safe-badge">Local session</span></div>
            <p className="rrugc-muted">Pinterest cookies stay on the Scout machine. Every candidate is analyzed first; only references that pass your filters can be saved to Managed Google Drive.</p>
            <div className="rrugc-form">
              <label>Name<input value={name} maxLength={200} onChange={event => setName(event.target.value)} /></label>
              <label>Search query<input value={query} maxLength={500} onChange={event => setQuery(event.target.value)} /></label>
              <div className="rrugc-form-row">
                <label>Target approved images<input type="number" min={1} max={5000} value={target} onChange={event => setTarget(Number(event.target.value))} /></label>
                <label>Scroll batches<input type="number" min={1} max={50} value={scrolls} onChange={event => setScrolls(Number(event.target.value))} /></label>
              </div>

              <details className="rrugc-filter-panel" open>
                <summary>Reference filters <span>Hat preset</span></summary>
                <div className="rrugc-filter-grid">
                  <label>Head size min<input type="number" min={5} max={90} value={minHeadRatio} onChange={event => setMinHeadRatio(Number(event.target.value))} /><small>% of image height</small></label>
                  <label>Head size max<input type="number" min={5} max={95} value={maxHeadRatio} onChange={event => setMaxHeadRatio(Number(event.target.value))} /><small>% of image height</small></label>
                  <label>Smile min<input type="number" min={0} max={100} value={minSmile} onChange={event => setMinSmile(Number(event.target.value))} /><small>score ≥</small></label>
                  <label>Head occlusion max<input type="number" min={0} max={100} value={maxOcclusion} onChange={event => setMaxOcclusion(Number(event.target.value))} /><small>score ≤</small></label>
                  <label>AI risk max<input type="number" min={0} max={100} value={maxAiRisk} onChange={event => setMaxAiRisk(Number(event.target.value))} /><small>risk ≤</small></label>
                  <label>Quality min<input type="number" min={0} max={100} value={minQuality} onChange={event => setMinQuality(Number(event.target.value))} /><small>score ≥</small></label>
                  <label>UGC style min<input type="number" min={0} max={100} value={minUgc} onChange={event => setMinUgc(Number(event.target.value))} /><small>score ≥</small></label>
                  <label>Hat fit min<input type="number" min={0} max={100} value={minProductFit} onChange={event => setMinProductFit(Number(event.target.value))} /><small>score ≥</small></label>
                </div>
                <div className="rrugc-filter-toggles">
                  <label className="rrugc-check"><input type="checkbox" checked={requireHeadVisible} onChange={event => setRequireHeadVisible(event.target.checked)} /><span>Require a clearly visible head</span></label>
                  <label className="rrugc-check"><input type="checkbox" checked={rejectHeadwear} onChange={event => setRejectHeadwear(event.target.checked)} /><span>Reject existing headwear</span></label>
                </div>
              </details>

              <label className="rrugc-check"><input type="checkbox" checked={autoImport} onChange={event => setAutoImport(event.target.checked)} /><span>Automatically save only approved references to Managed Google Drive</span></label>
              <button type="button" className="rrugc-primary" disabled={busy || !name.trim() || !query.trim()} onClick={() => void submit()}>{busy ? "Creating…" : "Create campaign"}</button>
            </div>

            {created && <div className="rrugc-command">
              <div><strong>Scout token created</strong><p>Shown once. Copy this command to the machine that has Chrome/Chromium and your Pinterest login.</p></div>
              <code>{command}</code>
              <button type="button" onClick={() => void copyCommand()}>{copied ? "Copied" : "Copy Scout command"}</button>
            </div>}
          </section>

          <section className="rrugc-card">
            <div className="rrugc-section-heading"><div><small>CAMPAIGNS</small><h2>Running work</h2></div><span>{campaigns.length}</span></div>
            {campaigns.length === 0 ? <p className="rrugc-empty">No campaign yet.</p> : <div className="rrugc-campaign-list">
              {campaigns.map(item => {
                const progressBase = item.auto_import ? item.drive_ready : item.approved;
                const progress = Math.min(100, Math.round((progressBase / item.target_count) * 100));
                return <button type="button" key={item.id} className={selectedId === item.id ? "active" : ""} onClick={() => setSelectedId(item.id)}>
                  <span><strong>{item.name}</strong><small>{item.query}</small></span>
                  <span className={"rrugc-agent status-" + item.scout_status}>{item.scout_status}</span>
                  <span className="rrugc-progress"><i style={{ width: progress + "%" }} /><small>{progressBase}/{item.target_count} target</small></span>
                  <span className="rrugc-campaign-stats"><small>{item.discovered} scanned</small><small>{item.analysis_pending + item.analyzing} pending</small><small>{item.approved} approved</small><small>{item.rejected} rejected</small></span>
                </button>;
              })}
            </div>}
          </section>
        </div>

        {selected && <section className="rrugc-card rrugc-live">
          <div className="rrugc-section-heading">
            <div><small>LIVE SCAN + ANALYSIS</small><h2>{selected.name}</h2><p>{selected.query}</p></div>
            <div className="rrugc-live-meta">
              <span>Scout: <b>{selected.scout_status}</b></span>
              <span>Pending: <b>{selected.analysis_pending + selected.analyzing}</b></span>
              <span>Approved: <b>{selected.approved}</b></span>
              <span>Rejected: <b>{selected.rejected}</b></span>
              <span>Drive: <b>{selected.drive_ready}</b></span>
              <span>Last seen: <b>{time(selected.scout_last_seen_at)}</b></span>
            </div>
          </div>
          <div className="rrugc-policy-strip">
            <span>Head <b>{Math.round(selected.min_head_ratio * 100)}–{Math.round(selected.max_head_ratio * 100)}%</b></span>
            <span>Smile <b>≥ {Math.round(selected.min_smile_score * 100)}%</b></span>
            <span>Occlusion <b>≤ {Math.round(selected.max_head_occlusion * 100)}%</b></span>
            <span>AI risk <b>≤ {Math.round(selected.max_ai_risk_score * 100)}%</b></span>
            <span>{selected.reject_headwear ? "No existing headwear" : "Headwear allowed"}</span>
          </div>
          {candidates.length === 0 ? <p className="rrugc-empty">Start the Browser Scout command to collect Pinterest candidates.</p> : <div className="rrugc-grid">
            {candidates.map(candidate => {
              const tone = candidateTone(candidate.status);
              const actionBusy = actionId === candidate.id;
              const canRetry = candidate.status === "analysis_failed" || rejectedStatuses.has(candidate.status);
              const canSave = candidate.status === "approved" || candidate.status === "import_failed";
              return <article key={candidate.id} className={"rrugc-candidate tone-" + tone}>
                <a href={candidate.pin_url} target="_blank" rel="noreferrer"><img src={candidate.image_url} alt={candidate.alt_text || "Pinterest reference candidate"} loading="lazy" referrerPolicy="no-referrer" /></a>
                <div className="rrugc-candidate-body">
                  <div className="rrugc-candidate-head"><strong>{statusLabel[candidate.status]}</strong>{candidate.final_score != null && <span>{percent(candidate.final_score)} fit</span>}</div>
                  {candidate.analysis_summary && <p>{candidate.analysis_summary}</p>}
                  {candidate.reject_reason && <p className="rrugc-reject-reason">{candidate.reject_reason.replaceAll("_", " ")}</p>}
                  {candidate.analyzed_at && <div className="rrugc-metrics">
                    <span>Head <b>{percent(candidate.primary_head_ratio)}</b></span>
                    <span>Smile <b>{percent(candidate.smile_score)}</b></span>
                    <span>UGC <b>{percent(candidate.mobile_ugc_score)}</b></span>
                    <span>Quality <b>{percent(candidate.quality_score)}</b></span>
                    <span>AI risk <b>{percent(candidate.ai_risk_score)}</b></span>
                    <span>Fit <b>{percent(candidate.product_fit_score)}</b></span>
                  </div>}
                  {!candidate.analyzed_at && <span className="rrugc-candidate-caption">{candidate.alt_text || "Pinterest candidate"}</span>}
                </div>
                <footer>
                  {candidate.web_url ? <a href={candidate.web_url} target="_blank" rel="noreferrer">Open in Drive</a>
                    : candidate.status === "import_queued" || candidate.status === "importing" ? <button type="button" disabled>Saving…</button>
                    : analyzingStatuses.has(candidate.status) ? <button type="button" disabled>Analyzing…</button>
                    : canSave ? <button type="button" disabled={Boolean(actionId)} onClick={() => void importOne(candidate)}>{actionBusy ? "Queueing…" : "Save to Drive"}</button>
                    : canRetry ? <button type="button" className="rrugc-secondary-action" disabled={Boolean(actionId)} onClick={() => void retryAnalysis(candidate)}>{actionBusy ? "Queueing…" : "Retry analysis"}</button>
                    : null}
                </footer>
              </article>;
            })}
          </div>}
        </section>}
      </div>
    </section>
  </main>;
}
