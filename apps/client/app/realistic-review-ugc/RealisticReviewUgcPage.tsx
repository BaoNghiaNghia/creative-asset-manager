import { useEffect, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import {
  analyzeCandidate,
  configureCampaignScoutAutomation,
  createCampaign,
  importCandidate,
  listCampaigns,
  listCandidates,
} from "./api";
import { CampaignGenerationPanel } from "./CampaignGenerationPanel";
import { PinterestAutoScoutPanel } from "./PinterestAutoScoutPanel";
import { DeliveryOperationsPanel } from "./DeliveryOperationsPanel";
import { ProductRegistryPanel } from "./ProductRegistryPanel";
import type { Campaign, Candidate, CandidateStatus } from "./types";
import "./ui-overhaul.css";

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
  const [name, setName] = useState("Pinterest lifestyle references");
  const [query, setQuery] = useState("happy woman casual outdoor candid");
  const [target, setTarget] = useState(100);
  const [scrolls, setScrolls] = useState(6);
  const [autoImport, setAutoImport] = useState(true);
  const [autoScout, setAutoScout] = useState(true);
  const [scanIntervalMinutes, setScanIntervalMinutes] = useState(5);
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
  const [candidateLimit, setCandidateLimit] = useState(24);
  const [error, setError] = useState("");

  const selected = campaigns.find(item => item.id === selectedId) || null;

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
    setCandidateLimit(24);
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
    try {
      const next = await createCampaign({
        name: name.trim(),
        query: query.trim(),
        target_count: target,
        max_scroll_batches: scrolls,
        auto_import: autoImport,
        auto_scout: autoScout,
        scan_interval_seconds: Math.max(60, Math.min(86400, scanIntervalMinutes * 60)),
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
      await refreshCampaigns();
      setSelectedId(next.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create campaign.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleAutoScout(campaign: Campaign) {
    if (actionId) return;
    setActionId("autoscout:" + campaign.id);
    setError("");
    try {
      const updated = await configureCampaignScoutAutomation(
        campaign.id,
        !campaign.auto_scout,
        campaign.scan_interval_seconds,
      );
      setCampaigns(rows => rows.map(row => row.id === updated.id ? updated : row));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update Auto Scout.");
    } finally {
      setActionId("");
    }
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
        description="Scout references, ground the product, generate, review, and deliver."
        titleAddon={<span className="rrugc-page-live-pill"><i aria-hidden="true" />Auto pipeline</span>}
        actions={<WorkspaceBackToAssets />}
      />
      <div className="rrugc-page-body">
        {error && <div className="rrugc-error" role="alert">{error}</div>}

        <section className="rrugc-overview" aria-label="Realistic Review UGC overview">
          <div className="rrugc-kpis">
            <article><span>Campaigns</span><strong>{kpis.workflows}</strong></article>
            <article><span>Active</span><strong>{kpis.running}</strong></article>
            <article><span>Approved refs</span><strong>{kpis.approved}</strong></article>
            <article><span>Drive ready</span><strong>{kpis.driveReady}</strong></article>
          </div>
        </section>

        <div id="rrugc-scout" className="rrugc-anchor-section">
          <PinterestAutoScoutPanel onError={setError} />
        </div>

        <div id="rrugc-campaigns" className="rrugc-columns rrugc-campaign-workspace">
          <details className="rrugc-card rrugc-create-campaign rrugc-compact-section">
            <summary className="rrugc-compact-section-summary">
              <span><small>NEW CAMPAIGN</small><strong>Define what Auto Scout should find</strong></span>
              <b>＋ New campaign</b>
            </summary>
            <div className="rrugc-form">
              <label>Name<input value={name} maxLength={200} onChange={event => setName(event.target.value)} /></label>
              <label>Search query<input value={query} maxLength={500} onChange={event => setQuery(event.target.value)} /></label>
              <div className="rrugc-form-row">
                <label>Target approved images<input type="number" min={1} max={5000} value={target} onChange={event => setTarget(Number(event.target.value))} /></label>
                <label>Scroll batches<input type="number" min={1} max={50} value={scrolls} onChange={event => setScrolls(Number(event.target.value))} /></label>
              </div>

              <details className="rrugc-filter-panel">
                <summary>
                  <span><strong>Qualification rules</strong><small>Head visibility, expression, quality, UGC style, AI risk</small></span>
                  <b>Hat preset</b>
                </summary>
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

              <div className="rrugc-auto-campaign-controls">
                <label className="rrugc-check">
                  <input type="checkbox" checked={autoScout} onChange={event => setAutoScout(event.target.checked)} />
                  <span>Auto Scout continuously until target is reached</span>
                </label>
                <label>
                  Rescan interval
                  <div className="rrugc-inline-number">
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={scanIntervalMinutes}
                      disabled={!autoScout}
                      onChange={event => setScanIntervalMinutes(Math.max(1, Number(event.target.value) || 1))}
                    />
                    <small>minutes</small>
                  </div>
                </label>
              </div>
              <label className="rrugc-check"><input type="checkbox" checked={autoImport} onChange={event => setAutoImport(event.target.checked)} /><span>Automatically save only approved references to Managed Google Drive</span></label>
              <div className="rrugc-form-submit">
                <span>New campaigns start immediately when Auto Scout is enabled and a paired Agent is online.</span>
                <button type="button" className="rrugc-primary" disabled={busy || !name.trim() || !query.trim()} onClick={() => void submit()}>{busy ? "Creating…" : "Create & start campaign"}</button>
              </div>
            </div>
          </details>

          <section className="rrugc-card rrugc-campaign-browser">
            <div className="rrugc-section-heading">
              <div><small>REFERENCE CAMPAIGNS</small><h2>Campaign queue</h2><p>Select a campaign to inspect live discovery, QA, production, and delivery.</p></div>
              <span className="rrugc-count-badge">{campaigns.length}</span>
            </div>
            {campaigns.length === 0 ? <p className="rrugc-empty">No campaign yet.</p> : <div className="rrugc-campaign-list">
              {campaigns.map(item => {
                const progressBase = item.auto_import ? item.drive_ready : item.approved;
                const progress = Math.min(100, Math.round((progressBase / item.target_count) * 100));
                return <button type="button" key={item.id} className={selectedId === item.id ? "active" : ""} onClick={() => setSelectedId(item.id)}>
                  <div className="rrugc-campaign-card-head">
                    <span className="rrugc-campaign-card-copy"><strong>{item.name}</strong><small>{item.query}</small></span>
                    <span className="rrugc-campaign-card-badges">
                      <span className={"rrugc-agent status-" + item.scout_status}>{item.scout_status.replaceAll("_", " ")}</span>
                      <span className={"rrugc-auto-mode " + (item.auto_scout ? "is-on" : "is-off")}>
                        {item.auto_scout ? "Auto" : "Paused"}
                      </span>
                    </span>
                  </div>
                  <div className="rrugc-campaign-progress-row">
                    <span className="rrugc-progress"><i style={{ width: progress + "%" }} /></span>
                    <strong>{progress}%</strong>
                    <small>{progressBase}/{item.target_count}</small>
                  </div>
                  <span className="rrugc-campaign-stats"><small><b>{item.discovered}</b> scanned</small><small><b>{item.analysis_pending + item.analyzing}</b> pending</small><small><b>{item.approved}</b> approved</small><small><b>{item.rejected}</b> rejected</small></span>
                </button>;
              })}
            </div>}
          </section>
        </div>

        <div id="rrugc-product" className="rrugc-anchor-section rrugc-secondary-workspace">
          <ProductRegistryPanel />
        </div>

        {selected && <section id="rrugc-production" className="rrugc-card rrugc-live rrugc-anchor-section">
          <div className="rrugc-section-heading rrugc-live-heading">
            <div><small>ACTIVE CAMPAIGN</small><h2>{selected.name}</h2><p>{selected.query}</p></div>
            <div className="rrugc-live-meta rrugc-live-meta-primary">
              <span>Pending <b>{selected.analysis_pending + selected.analyzing}</b></span>
              <span>Approved <b>{selected.approved}</b></span>
              <span>Drive <b>{selected.drive_ready}</b></span>
              <span>Scout <b>{selected.auto_scout ? "On" : "Off"}</b></span>
            </div>
          </div>
          <div className="rrugc-scan-control-strip">
            <div>
              <strong>{selected.auto_scout ? "Continuous Pinterest pull enabled" : "Auto Scout paused"}</strong>
              <small>
                {selected.scan_last_error_code
                  ? "Last error: " + selected.scan_last_error_code.replaceAll("_", " ")
                  : "Rescan every " + Math.round(selected.scan_interval_seconds / 60) + " min until target."}
              </small>
            </div>
            <button
              type="button"
              disabled={Boolean(actionId)}
              onClick={() => void toggleAutoScout(selected)}
            >
              {actionId === "autoscout:" + selected.id
                ? "Updating…"
                : selected.auto_scout ? "Pause Auto Scout" : "Resume Auto Scout"}
            </button>
          </div>
          <details className="rrugc-inline-disclosure">
            <summary>Scan details & filters</summary>
            <div className="rrugc-live-meta rrugc-live-meta-secondary">
              <span>Status <b>{selected.scout_status}</b></span>
              <span>Next scan <b>{selected.auto_scout ? time(selected.scan_next_at) : "Paused"}</b></span>
              <span>Runs <b>{selected.scan_attempt_count}</b></span>
              <span>Last seen <b>{time(selected.scout_last_seen_at)}</b></span>
            </div>
            <div className="rrugc-policy-strip">
              <span>Head <b>{Math.round(selected.min_head_ratio * 100)}–{Math.round(selected.max_head_ratio * 100)}%</b></span>
              <span>Smile <b>≥ {Math.round(selected.min_smile_score * 100)}%</b></span>
              <span>Occlusion <b>≤ {Math.round(selected.max_head_occlusion * 100)}%</b></span>
              <span>AI risk <b>≤ {Math.round(selected.max_ai_risk_score * 100)}%</b></span>
              <span>{selected.reject_headwear ? "No existing headwear" : "Headwear allowed"}</span>
            </div>
          </details>
          <section className="rrugc-reference-workspace" aria-label="Reference qualification">
            <div className="rrugc-subsection-heading">
              <div><small>REFERENCE QUALIFICATION</small><h3>Pinterest candidates</h3><p>Review what Auto Scout found and how each image scored before using it as a durable person reference.</p></div>
              <span>{candidates.length} found</span>
            </div>
          {candidates.length === 0 ? <p className="rrugc-empty">
            {selected.auto_scout
              ? "Waiting for the paired Auto Scout to collect Pinterest candidates."
              : "Auto Scout is paused for this campaign."}
          </p> : <div className="rrugc-grid">
            {candidates.slice(0, candidateLimit).map(candidate => {
              const tone = candidateTone(candidate.status);
              const actionBusy = actionId === candidate.id;
              const canRetry = candidate.status === "analysis_failed" || rejectedStatuses.has(candidate.status);
              const canSave = candidate.status === "approved" || candidate.status === "import_failed";
              return <article key={candidate.id} className={"rrugc-candidate tone-" + tone}>
                <div className="rrugc-candidate-media">
                  <a href={candidate.pin_url} target="_blank" rel="noreferrer"><img src={candidate.image_url} alt={candidate.alt_text || "Pinterest reference candidate"} loading="lazy" referrerPolicy="no-referrer" /></a>
                  <span className={"rrugc-candidate-status tone-" + tone}>{statusLabel[candidate.status]}</span>
                  {candidate.final_score != null && <span className="rrugc-candidate-score">{percent(candidate.final_score)} fit</span>}
                </div>
                <div className="rrugc-candidate-body">
                  <div className="rrugc-candidate-head"><strong>Reference analysis</strong><span>{candidate.analyzed_at ? "Scored" : "Pending"}</span></div>
                  {candidate.reject_reason && <p className="rrugc-reject-reason">{candidate.reject_reason.replaceAll("_", " ")}</p>}
                  {candidate.analyzed_at ? <details className="rrugc-candidate-details">
                    <summary>Analysis details</summary>
                    {candidate.analysis_summary && <p>{candidate.analysis_summary}</p>}
                    <div className="rrugc-metrics">
                      <span>Head <b>{percent(candidate.primary_head_ratio)}</b></span>
                      <span>Smile <b>{percent(candidate.smile_score)}</b></span>
                      <span>UGC <b>{percent(candidate.mobile_ugc_score)}</b></span>
                      <span>Quality <b>{percent(candidate.quality_score)}</b></span>
                      <span>AI risk <b>{percent(candidate.ai_risk_score)}</b></span>
                      <span>Fit <b>{percent(candidate.product_fit_score)}</b></span>
                    </div>
                  </details> : <span className="rrugc-candidate-caption">{candidate.alt_text || "Pinterest candidate"}</span>}
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
          {candidates.length > candidateLimit && <button
            type="button"
            className="rrugc-show-more"
            onClick={() => setCandidateLimit(limit => limit + 24)}
          >
            Show 24 more · {candidates.length - candidateLimit} remaining
          </button>}
          </section>
          <CampaignGenerationPanel
            campaign={selected}
            candidates={candidates}
            onCampaignChanged={updated => {
              setCampaigns(rows => rows.map(row => row.id === updated.id ? updated : row));
            }}
            onError={setError}
          />
        </section>}

        <div id="rrugc-operations" className="rrugc-anchor-section rrugc-secondary-workspace">
          <DeliveryOperationsPanel onError={setError} />
        </div>
      </div>
    </section>
  </main>;
}
