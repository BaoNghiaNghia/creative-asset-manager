import { useEffect, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import {
  analyzeCandidate,
  configureCampaignScoutAutomation,
  createCampaign,
  deleteCampaign,
  importCandidate,
  markCandidateAiFeedback,
  listCampaigns,
  listCandidates,
  updateCampaign,
} from "./api";
import { CampaignGenerationPanel } from "./CampaignGenerationPanel";
import { PinterestAutoScoutPanel } from "./PinterestAutoScoutPanel";
import { DeliveryOperationsPanel } from "./DeliveryOperationsPanel";
import { ProductRegistryPanel } from "./ProductRegistryPanel";
import { referenceLifestyleSearchQueries } from "./searchPresets";
import type { AiManualLabel, Campaign, Candidate, CandidateStatus } from "./types";
import "./ui-overhaul.css";

const time = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";
const percent = (value: number | null | undefined) => value == null ? "—" : Math.round(value * 100) + "%";

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

const driveStatuses = new Set<CandidateStatus>([
  "import_queued",
  "importing",
  "drive_ready",
  "import_failed",
]);

const analysisApprovedStatuses = new Set<CandidateStatus>([
  "approved",
  ...driveStatuses,
]);

export type CandidateGalleryTab = "approved" | "rejected" | "drive" | "processing";

export function candidateMatchesGalleryTab(
  status: CandidateStatus,
  tab: CandidateGalleryTab,
): boolean {
  if (tab === "approved") return analysisApprovedStatuses.has(status);
  if (tab === "drive") return driveStatuses.has(status);
  if (tab === "rejected") {
    return rejectedStatuses.has(status) || status === "rejected_duplicate";
  }
  return analyzingStatuses.has(status) || status === "analysis_failed";
}

export function candidateGalleryTab(status: CandidateStatus): CandidateGalleryTab {
  if (driveStatuses.has(status)) return "drive";
  if (status === "approved") return "approved";
  if (rejectedStatuses.has(status) || status === "rejected_duplicate") return "rejected";
  return "processing";
}

function candidateTone(status: CandidateStatus): string {
  if (status === "drive_ready" || status === "approved") return "positive";
  if (rejectedStatuses.has(status) || status === "analysis_failed" || status === "import_failed") return "negative";
  if (status === "rejected_duplicate") return "neutral";
  return "working";
}

function SearchQueryEditor({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function add() {
    const keyword = draft.trim();
    if (!keyword || value.length >= 10) return;
    if (value.some(item => item.toLocaleLowerCase() === keyword.toLocaleLowerCase())) {
      setDraft("");
      return;
    }
    onChange([...value, keyword]);
    setDraft("");
  }

  return <div className="rrugc-keyword-editor">
    <div className="rrugc-keyword-preset">
      <span>
        <strong>Real-person lifestyle preset</strong>
        <small>8 people-first searches + 2 optional hat searches</small>
      </span>
      <button type="button" onClick={() => onChange(referenceLifestyleSearchQueries())}>
        Use preset
      </button>
    </div>
    <div className="rrugc-keyword-chips">
      {value.map((keyword, index) => <span key={keyword + index}>
        {keyword}
        <button
          type="button"
          aria-label={"Remove " + keyword}
          onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}
        >×</button>
      </span>)}
      {value.length === 0 && <small>Add at least one Pinterest search keyword.</small>}
    </div>
    <div className="rrugc-keyword-input">
      <input
        value={draft}
        maxLength={500}
        placeholder={value.length ? "Add another search keyword…" : "authentic candid lifestyle portrait"}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => {
          if (event.key === "Enter") {
            event.preventDefault();
            add();
          }
        }}
      />
      <button type="button" disabled={!draft.trim() || value.length >= 10} onClick={add}>Add</button>
    </div>
    <small>{value.length}/10 keywords · Auto Scout searches every keyword in each scan.</small>
  </div>;
}

type CampaignEditDraft = {
  name: string;
  searchQueries: string[];
  target: number;
  scrolls: number;
  autoImport: boolean;
  autoScout: boolean;
  scanIntervalMinutes: number;
  minHeadRatio: number;
  maxHeadRatio: number;
  minSmile: number;
  maxOcclusion: number;
  maxAiRisk: number;
  minQuality: number;
  minUgc: number;
  minProductFit: number;
  requireHeadVisible: boolean;
  rejectHeadwear: boolean;
};

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
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("Pinterest lifestyle references");
  const [searchQueries, setSearchQueries] = useState<string[]>(() => referenceLifestyleSearchQueries());
  const [target, setTarget] = useState(100);
  const [scrolls, setScrolls] = useState(6);
  const [autoImport, setAutoImport] = useState(true);
  const [autoScout, setAutoScout] = useState(true);
  const [scanIntervalMinutes, setScanIntervalMinutes] = useState(5);
  const [minHeadRatio, setMinHeadRatio] = useState(20);
  const [maxHeadRatio, setMaxHeadRatio] = useState(45);
  const [minSmile, setMinSmile] = useState(65);
  const [maxOcclusion, setMaxOcclusion] = useState(25);
  const [maxAiRisk, setMaxAiRisk] = useState(15);
  const [minQuality, setMinQuality] = useState(60);
  const [minUgc, setMinUgc] = useState(65);
  const [minProductFit, setMinProductFit] = useState(55);
  const [rejectHeadwear, setRejectHeadwear] = useState(false);
  const [requireHeadVisible, setRequireHeadVisible] = useState(true);
  const [busy, setBusy] = useState(false);
  const [actionId, setActionId] = useState("");
  const [candidateTab, setCandidateTab] = useState<CandidateGalleryTab>("approved");
  const [candidateLimit, setCandidateLimit] = useState(24);
  const [editingId, setEditingId] = useState("");
  const [editDraft, setEditDraft] = useState<CampaignEditDraft | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
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
    if (!name.trim() || searchQueries.length === 0 || busy) return;
    if (minHeadRatio >= maxHeadRatio) {
      setError("Minimum head ratio must be lower than maximum head ratio.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await createCampaign({
        name: name.trim(),
        query: searchQueries[0],
        search_queries: searchQueries,
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
      setCreateOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create campaign.");
    } finally {
      setBusy(false);
    }
  }

  async function removeCampaign(campaign: Campaign) {
    if (actionId) return;
    const confirmed = window.confirm(
      `Delete campaign "${campaign.name}"? It will be removed from the campaign list and Auto Scout will stop. Existing campaign history is preserved.`,
    );
    if (!confirmed) return;
    setActionId("delete:" + campaign.id);
    setError("");
    try {
      await deleteCampaign(campaign.id);
      await refreshCampaigns();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to delete campaign.");
    } finally {
      setActionId("");
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

  async function markAiFeedback(candidate: Candidate, label: AiManualLabel) {
    if (!selected || actionId) return;
    setActionId("ai:" + candidate.id);
    setError("");
    try {
      const result = await markCandidateAiFeedback(selected.id, candidate.id, label);
      setCandidates(rows => rows.map(row => row.id === result.candidate.id ? result.candidate : row));
      await refreshCampaigns();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save AI review.");
    } finally {
      setActionId("");
    }
  }

  function openCampaignEditor(campaign: Campaign) {
    setEditingId(campaign.id);
    setEditDraft({
      name: campaign.name,
      searchQueries: campaign.search_queries?.length ? campaign.search_queries : [campaign.query],
      target: campaign.target_count,
      scrolls: campaign.max_scroll_batches,
      autoImport: campaign.auto_import,
      autoScout: campaign.auto_scout,
      scanIntervalMinutes: Math.max(1, Math.round(campaign.scan_interval_seconds / 60)),
      minHeadRatio: Math.round(campaign.min_head_ratio * 100),
      maxHeadRatio: Math.round(campaign.max_head_ratio * 100),
      minSmile: Math.round(campaign.min_smile_score * 100),
      maxOcclusion: Math.round(campaign.max_head_occlusion * 100),
      maxAiRisk: Math.round(campaign.max_ai_risk_score * 100),
      minQuality: Math.round(campaign.min_quality_score * 100),
      minUgc: Math.round(campaign.min_ugc_score * 100),
      minProductFit: Math.round(campaign.min_product_fit_score * 100),
      requireHeadVisible: campaign.require_head_visible,
      rejectHeadwear: campaign.reject_headwear,
    });
  }

  async function saveCampaignEdit() {
    if (!editingId || !editDraft || savingEdit) return;
    if (!editDraft.name.trim() || editDraft.searchQueries.length === 0) {
      setError("Campaign name and at least one search keyword are required.");
      return;
    }
    if (editDraft.minHeadRatio >= editDraft.maxHeadRatio) {
      setError("Minimum head ratio must be lower than maximum head ratio.");
      return;
    }
    setSavingEdit(true);
    setError("");
    try {
      const updated = await updateCampaign(editingId, {
        name: editDraft.name.trim(),
        search_queries: editDraft.searchQueries,
        target_count: editDraft.target,
        max_scroll_batches: editDraft.scrolls,
        auto_import: editDraft.autoImport,
        auto_scout: editDraft.autoScout,
        scan_interval_seconds: Math.max(60, Math.min(86400, editDraft.scanIntervalMinutes * 60)),
        min_head_ratio: editDraft.minHeadRatio / 100,
        max_head_ratio: editDraft.maxHeadRatio / 100,
        min_smile_score: editDraft.minSmile / 100,
        max_head_occlusion: editDraft.maxOcclusion / 100,
        max_ai_risk_score: editDraft.maxAiRisk / 100,
        min_quality_score: editDraft.minQuality / 100,
        min_ugc_score: editDraft.minUgc / 100,
        min_product_fit_score: editDraft.minProductFit / 100,
        require_head_visible: editDraft.requireHeadVisible,
        reject_headwear: editDraft.rejectHeadwear,
      });
      setCampaigns(rows => rows.map(row => row.id === updated.id ? updated : row));
      setEditingId("");
      setEditDraft(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update campaign.");
    } finally {
      setSavingEdit(false);
    }
  }

  const kpis = {
    workflows: campaigns.length,
    running: campaigns.filter(item => item.status === "running").length,
    approved: campaigns.reduce((sum, item) => sum + item.approved, 0),
    driveReady: campaigns.reduce((sum, item) => sum + item.drive_ready, 0),
  };

  const candidateGroups: Record<CandidateGalleryTab, Candidate[]> = {
    approved: [],
    rejected: [],
    drive: [],
    processing: [],
  };
  for (const candidate of candidates) {
    (Object.keys(candidateGroups) as CandidateGalleryTab[]).forEach(tab => {
      if (candidateMatchesGalleryTab(candidate.status, tab)) {
        candidateGroups[tab].push(candidate);
      }
    });
  }
  const visibleCandidates = candidateGroups[candidateTab];
  const candidateEmptyCopy: Record<CandidateGalleryTab, string> = {
    approved: "No references have passed qualification yet.",
    rejected: "No references were rejected by qualification rules.",
    drive: "No references are queued, saving, ready, or failed in Drive yet.",
    processing: "No references are waiting for analysis, analyzing, or waiting for retry.",
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

        <section id="rrugc-campaigns" className="rrugc-card rrugc-campaign-browser rrugc-campaign-manager">
          <div className="rrugc-section-heading">
            <div>
              <small>REFERENCE CAMPAIGNS</small>
              <h2>Campaigns</h2>
              <p>Add, edit, delete, and select campaigns from one place.</p>
            </div>
            <div className="rrugc-campaign-heading-actions">
              <span className="rrugc-count-badge">{campaigns.length}</span>
              <button
                type="button"
                className={createOpen ? "rrugc-campaign-add is-open" : "rrugc-campaign-add"}
                onClick={() => setCreateOpen(value => !value)}
                aria-expanded={createOpen}
              >
                {createOpen ? "Close" : "＋ Add campaign"}
              </button>
            </div>
          </div>

          {createOpen && <div className="rrugc-create-campaign-panel">
            <div className="rrugc-create-campaign-heading">
              <div><small>NEW CAMPAIGN</small><strong>Define what Auto Scout should find</strong></div>
              <button type="button" onClick={() => setCreateOpen(false)} aria-label="Close new campaign form">×</button>
            </div>
            <div className="rrugc-form">
              <label>Name<input value={name} maxLength={200} onChange={event => setName(event.target.value)} /></label>
              <label>
                Search keywords
                <SearchQueryEditor value={searchQueries} onChange={setSearchQueries} />
              </label>
              <div className="rrugc-form-row">
                <label>Target approved images<input type="number" min={1} max={5000} value={target} onChange={event => setTarget(Number(event.target.value))} /></label>
                <label>Scroll batches<input type="number" min={1} max={50} value={scrolls} onChange={event => setScrolls(Number(event.target.value))} /></label>
              </div>

              <details className="rrugc-filter-panel">
                <summary>
                  <span><strong>Qualification rules</strong><small>Quality-first real photos: AI risk ≤ 15%, quality ≥ 60%, UGC ≥ 65%</small></span>
                  <b>Real photo gate</b>
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
                <button type="button" className="rrugc-primary" disabled={busy || !name.trim() || searchQueries.length === 0} onClick={() => void submit()}>{busy ? "Creating…" : "Create & start campaign"}</button>
              </div>
            </div>
          </div>}

          {campaigns.length === 0 ? <p className="rrugc-empty">No campaign yet. Add a campaign to start scouting Pinterest references.</p> : <div className="rrugc-campaign-list">
            {campaigns.map(item => {
              const progressBase = item.auto_import ? item.drive_ready : item.approved;
              const progress = Math.min(100, Math.round((progressBase / item.target_count) * 100));
              const keywords = item.search_queries?.length ? item.search_queries : [item.query];
              const deleting = actionId === "delete:" + item.id;
              return <article key={item.id} className={selectedId === item.id ? "active" : ""}>
                <button type="button" className="rrugc-campaign-select" onClick={() => setSelectedId(item.id)}>
                  <div className="rrugc-campaign-card-head">
                    <span className="rrugc-campaign-card-copy"><strong>{item.name}</strong></span>
                    <span className="rrugc-campaign-card-badges">
                      <span className={"rrugc-agent status-" + item.scout_status}>{item.scout_status.replaceAll("_", " ")}</span>
                      <span className={"rrugc-auto-mode " + (item.auto_scout ? "is-on" : "is-off")}>
                        {item.auto_scout ? "Auto" : "Paused"}
                      </span>
                    </span>
                  </div>
                  <span className="rrugc-campaign-keywords">
                    {keywords.slice(0, 3).map(keyword => <small key={keyword}>{keyword}</small>)}
                    {keywords.length > 3 && <small>+{keywords.length - 3}</small>}
                  </span>
                  <div className="rrugc-campaign-progress-row">
                    <span className="rrugc-progress"><i style={{ width: progress + "%" }} /></span>
                    <strong>{progress}%</strong>
                    <small>{progressBase}/{item.target_count}</small>
                  </div>
                  <span className="rrugc-campaign-stats"><small><b>{item.discovered}</b> scanned</small><small><b>{item.analysis_pending + item.analyzing}</b> pending</small><small><b>{item.approved}</b> approved</small><small><b>{item.rejected}</b> rejected</small></span>
                </button>
                <footer className="rrugc-campaign-actions">
                  <button type="button" disabled={Boolean(actionId)} onClick={() => openCampaignEditor(item)}>Edit</button>
                  <button
                    type="button"
                    className="rrugc-campaign-delete"
                    disabled={Boolean(actionId)}
                    onClick={() => void removeCampaign(item)}
                  >
                    {deleting ? "Deleting…" : "Delete"}
                  </button>
                </footer>
              </article>;
            })}
          </div>}
        </section>

        {editingId && editDraft && <div className="rrugc-campaign-editor-backdrop" role="presentation" onMouseDown={() => {
          if (!savingEdit) {
            setEditingId("");
            setEditDraft(null);
          }
        }}>
          <section className="rrugc-campaign-editor" role="dialog" aria-modal="true" aria-label="Edit campaign" onMouseDown={event => event.stopPropagation()}>
            <header>
              <div><small>EDIT CAMPAIGN</small><h2>{editDraft.name || "Campaign"}</h2></div>
              <button type="button" aria-label="Close editor" disabled={savingEdit} onClick={() => {
                setEditingId("");
                setEditDraft(null);
              }}>×</button>
            </header>
            <div className="rrugc-campaign-editor-body">
              <section className="rrugc-editor-section">
                <div className="rrugc-editor-section-heading">
                  <div><small>BASIC</small><strong>Campaign setup</strong></div>
                  <span>Core settings</span>
                </div>
                <label className="rrugc-editor-field">
                  <span>Campaign name</span>
                  <input value={editDraft.name} maxLength={200} onChange={event => setEditDraft(current => current ? { ...current, name: event.target.value } : current)} />
                </label>
                <div className="rrugc-editor-number-grid">
                  <label className="rrugc-editor-field">
                    <span>Target approved</span>
                    <input type="number" min={1} max={5000} value={editDraft.target} onChange={event => setEditDraft(current => current ? { ...current, target: Number(event.target.value) } : current)} />
                    <small>images</small>
                  </label>
                  <label className="rrugc-editor-field">
                    <span>Scrolls / keyword</span>
                    <input type="number" min={1} max={50} value={editDraft.scrolls} onChange={event => setEditDraft(current => current ? { ...current, scrolls: Number(event.target.value) } : current)} />
                    <small>batches</small>
                  </label>
                  <label className="rrugc-editor-field">
                    <span>Rescan every</span>
                    <input type="number" min={1} max={1440} disabled={!editDraft.autoScout} value={editDraft.scanIntervalMinutes} onChange={event => setEditDraft(current => current ? { ...current, scanIntervalMinutes: Math.max(1, Number(event.target.value) || 1) } : current)} />
                    <small>minutes</small>
                  </label>
                </div>
              </section>

              <section className="rrugc-editor-section">
                <div className="rrugc-editor-section-heading">
                  <div><small>DISCOVERY</small><strong>Pinterest search keywords</strong></div>
                  <span>{editDraft.searchQueries.length}/10</span>
                </div>
                <SearchQueryEditor
                  value={editDraft.searchQueries}
                  onChange={searchQueries => setEditDraft(current => current ? { ...current, searchQueries } : current)}
                />
              </section>

              <section className="rrugc-editor-section">
                <div className="rrugc-editor-section-heading">
                  <div><small>AUTOMATION</small><strong>Scout & delivery</strong></div>
                  <span>{editDraft.autoScout ? "Running" : "Paused"}</span>
                </div>
                <div className="rrugc-editor-toggle-grid">
                  <label className="rrugc-editor-toggle">
                    <input type="checkbox" checked={editDraft.autoScout} onChange={event => setEditDraft(current => current ? { ...current, autoScout: event.target.checked } : current)} />
                    <span><strong>Auto Scout</strong><small>Keep scanning until the campaign target is reached.</small></span>
                  </label>
                  <label className="rrugc-editor-toggle">
                    <input type="checkbox" checked={editDraft.autoImport} onChange={event => setEditDraft(current => current ? { ...current, autoImport: event.target.checked } : current)} />
                    <span><strong>Save approved to Drive</strong><small>Automatically persist approved references to Managed Drive.</small></span>
                  </label>
                </div>
              </section>

              <details className="rrugc-filter-panel rrugc-editor-advanced">
                <summary><span><strong>Qualification rules</strong><small>Real-photo gate plus head visibility, smile, quality, UGC style, and product fit</small></span><b>Quality-first</b></summary>
                <div className="rrugc-filter-grid">
                  <label>Head min<input type="number" min={5} max={90} value={editDraft.minHeadRatio} onChange={event => setEditDraft(current => current ? { ...current, minHeadRatio: Number(event.target.value) } : current)} /></label>
                  <label>Head max<input type="number" min={5} max={95} value={editDraft.maxHeadRatio} onChange={event => setEditDraft(current => current ? { ...current, maxHeadRatio: Number(event.target.value) } : current)} /></label>
                  <label>Smile min<input type="number" min={0} max={100} value={editDraft.minSmile} onChange={event => setEditDraft(current => current ? { ...current, minSmile: Number(event.target.value) } : current)} /></label>
                  <label>Occlusion max<input type="number" min={0} max={100} value={editDraft.maxOcclusion} onChange={event => setEditDraft(current => current ? { ...current, maxOcclusion: Number(event.target.value) } : current)} /></label>
                  <label>AI risk max<input type="number" min={0} max={100} value={editDraft.maxAiRisk} onChange={event => setEditDraft(current => current ? { ...current, maxAiRisk: Number(event.target.value) } : current)} /></label>
                  <label>Quality min<input type="number" min={0} max={100} value={editDraft.minQuality} onChange={event => setEditDraft(current => current ? { ...current, minQuality: Number(event.target.value) } : current)} /></label>
                  <label>UGC min<input type="number" min={0} max={100} value={editDraft.minUgc} onChange={event => setEditDraft(current => current ? { ...current, minUgc: Number(event.target.value) } : current)} /></label>
                  <label>Product fit min<input type="number" min={0} max={100} value={editDraft.minProductFit} onChange={event => setEditDraft(current => current ? { ...current, minProductFit: Number(event.target.value) } : current)} /></label>
                </div>
                <div className="rrugc-filter-toggles">
                  <label className="rrugc-check"><input type="checkbox" checked={editDraft.requireHeadVisible} onChange={event => setEditDraft(current => current ? { ...current, requireHeadVisible: event.target.checked } : current)} /><span>Require visible head</span></label>
                  <label className="rrugc-check"><input type="checkbox" checked={editDraft.rejectHeadwear} onChange={event => setEditDraft(current => current ? { ...current, rejectHeadwear: event.target.checked } : current)} /><span>Reject existing headwear</span></label>
                </div>
              </details>
            </div>
            <footer>
              <span>Keyword edits are used on the next Scout scan. Existing candidates are preserved.</span>
              <div>
                <button type="button" disabled={savingEdit} onClick={() => {
                  setEditingId("");
                  setEditDraft(null);
                }}>Cancel</button>
                <button type="button" className="rrugc-primary" disabled={savingEdit || !editDraft.name.trim() || editDraft.searchQueries.length === 0} onClick={() => void saveCampaignEdit()}>
                  {savingEdit ? "Saving…" : "Save campaign"}
                </button>
              </div>
            </footer>
          </section>
        </div>}

        <div id="rrugc-product" className="rrugc-anchor-section rrugc-secondary-workspace">
          <ProductRegistryPanel />
        </div>

        {selected && <section id="rrugc-production" className="rrugc-card rrugc-live rrugc-anchor-section">
          <div className="rrugc-section-heading rrugc-live-heading">
            <div>
              <small>ACTIVE CAMPAIGN</small>
              <h2>{selected.name}</h2>
              <span className="rrugc-active-keywords">
                {(selected.search_queries?.length ? selected.search_queries : [selected.query]).map(keyword => <small key={keyword}>{keyword}</small>)}
              </span>
            </div>
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
              <div><small>REFERENCE QUALIFICATION</small><h3>Pinterest candidates</h3><p>Review approved references, rejected results, and Drive imports separately.</p></div>
              <span>{candidates.length} found</span>
            </div>
            <div className="rrugc-candidate-tabs" role="tablist" aria-label="Candidate status">
              {([
                ["approved", "Approved"],
                ["rejected", "Rejected"],
                ["drive", "Drive"],
              ] as const).map(([tab, label]) => <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={candidateTab === tab}
                className={candidateTab === tab ? "is-active" : ""}
                onClick={() => {
                  setCandidateTab(tab);
                  setCandidateLimit(24);
                }}
              >
                <span>{label}</span><b>{candidateGroups[tab].length}</b>
              </button>)}
              {candidateGroups.processing.length > 0 && <button
                type="button"
                role="tab"
                aria-selected={candidateTab === "processing"}
                className={candidateTab === "processing" ? "is-active" : ""}
                onClick={() => {
                  setCandidateTab("processing");
                  setCandidateLimit(24);
                }}
              >
                <span>Processing</span><b>{candidateGroups.processing.length}</b>
              </button>}
            </div>
          {candidates.length === 0 ? <p className="rrugc-empty">
            {selected.auto_scout
              ? "Waiting for the paired Auto Scout to collect Pinterest candidates."
              : "Auto Scout is paused for this campaign."}
          </p> : visibleCandidates.length === 0 ? <p className="rrugc-empty rrugc-tab-empty">
            {candidateEmptyCopy[candidateTab]}
          </p> : <div className="rrugc-grid rrugc-masonry-grid" role="tabpanel">
            {visibleCandidates.slice(0, candidateLimit).map(candidate => {
              const tone = candidateTone(candidate.status);
              const actionBusy = actionId === candidate.id;
              const aiReviewBusy = actionId === "ai:" + candidate.id;
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
                      <span>AI confidence <b>{percent(candidate.ai_detector_confidence)}</b></span>
                      <span>Fit <b>{percent(candidate.product_fit_score)}</b></span>
                    </div>
                  </details> : <span className="rrugc-candidate-caption">{candidate.alt_text || "Pinterest candidate"}</span>}
                  <div className="rrugc-ai-review">
                    <div className="rrugc-ai-review-head">
                      <span>Human authenticity review</span>
                      {candidate.ai_manual_label
                        ? <b className={"is-" + candidate.ai_manual_label}>
                          {candidate.ai_manual_label === "real" ? "Marked real" : candidate.ai_manual_label === "ai" ? "Marked AI" : "Unsure"}
                        </b>
                        : candidate.ai_risk_confirmed
                          ? <b className="is-ai">AI signals confirmed</b>
                          : <b>Not reviewed</b>}
                    </div>
                    <div className="rrugc-ai-review-actions" role="group" aria-label="Mark image authenticity">
                      {([
                        ["real", "Real photo"],
                        ["ai", "AI"],
                        ["unsure", "Unsure"],
                      ] as const).map(([label, copy]) => <button
                        key={label}
                        type="button"
                        className={candidate.ai_manual_label === label ? "is-active is-" + label : ""}
                        aria-pressed={candidate.ai_manual_label === label}
                        disabled={Boolean(actionId)}
                        onClick={() => void markAiFeedback(candidate, label)}
                      >
                        {aiReviewBusy ? "Saving…" : copy}
                      </button>)}
                    </div>
                  </div>
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
          {visibleCandidates.length > candidateLimit && <button
            type="button"
            className="rrugc-show-more"
            onClick={() => setCandidateLimit(limit => limit + 24)}
          >
            Show 24 more · {visibleCandidates.length - candidateLimit} remaining
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
