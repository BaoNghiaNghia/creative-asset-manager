import { useEffect, useMemo, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspaceBackToAssets, WorkspacePageHeader } from "../components/WorkspacePageHeader";
import { createCampaign, importCandidate, listCampaigns, listCandidates } from "./api";
import type { Campaign, CampaignCreated, Candidate } from "./types";

const time = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";
const statusLabel: Record<Candidate["status"], string> = {
  discovered: "Discovered",
  importing: "Importing",
  drive_ready: "Drive ready",
  import_failed: "Import failed",
  rejected_duplicate: "Duplicate",
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
  const [created, setCreated] = useState<CampaignCreated | null>(null);
  const [name, setName] = useState("Pinterest lifestyle references");
  const [query, setQuery] = useState("happy woman casual outdoor candid");
  const [target, setTarget] = useState(100);
  const [scrolls, setScrolls] = useState(6);
  const [autoImport, setAutoImport] = useState(true);
  const [busy, setBusy] = useState(false);
  const [importingId, setImportingId] = useState("");
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
    void listCandidates(selectedId, controller.signal)
      .then(setCandidates)
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load candidates.");
      });
    const timer = window.setInterval(() => {
      if (!document.hidden) void listCandidates(selectedId).then(setCandidates).catch(() => undefined);
    }, 5000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [selectedId]);

  async function submit() {
    if (!name.trim() || !query.trim() || busy) return;
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

  async function importOne(candidate: Candidate) {
    if (!selected || importingId) return;
    setImportingId(candidate.id);
    setError("");
    try {
      const result = await importCandidate(selected.id, candidate.id);
      setCandidates(rows => rows.map(row => row.id === result.candidate.id ? result.candidate : row));
      await refreshCampaigns();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to import this image.");
    } finally {
      setImportingId("");
    }
  }

  const kpis = {
    workflows: campaigns.length,
    running: campaigns.filter(item => item.status === "running").length,
    discovered: campaigns.reduce((sum, item) => sum + item.discovered, 0),
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
        <article><span>Discovered</span><strong>{kpis.discovered}</strong></article>
        <article><span>Drive ready</span><strong>{kpis.driveReady}</strong></article>
      </section>

      <div className="rrugc-columns">
        <section className="rrugc-card">
          <div className="rrugc-section-heading"><div><small>NEW CAMPAIGN</small><h2>Pinterest Browser Scout</h2></div><span className="rrugc-safe-badge">Local session</span></div>
          <p className="rrugc-muted">Pinterest cookies stay on the Scout machine. CAM receives candidate Pin URLs, pinimg image URLs and visible metadata only.</p>
          <div className="rrugc-form">
            <label>Name<input value={name} maxLength={200} onChange={event => setName(event.target.value)} /></label>
            <label>Search query<input value={query} maxLength={500} onChange={event => setQuery(event.target.value)} /></label>
            <div className="rrugc-form-row">
              <label>Target images<input type="number" min={1} max={5000} value={target} onChange={event => setTarget(Number(event.target.value))} /></label>
              <label>Scroll batches<input type="number" min={1} max={50} value={scrolls} onChange={event => setScrolls(Number(event.target.value))} /></label>
            </div>
            <label className="rrugc-check"><input type="checkbox" checked={autoImport} onChange={event => setAutoImport(event.target.checked)} /><span>Automatically import scanned candidates to Managed Google Drive</span></label>
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
              const progressBase = item.auto_import ? item.drive_ready : item.discovered;
              const progress = Math.min(100, Math.round((progressBase / item.target_count) * 100));
              return <button type="button" key={item.id} className={selectedId === item.id ? "active" : ""} onClick={() => setSelectedId(item.id)}>
                <span><strong>{item.name}</strong><small>{item.query}</small></span>
                <span className={"rrugc-agent status-" + item.scout_status}>{item.scout_status}</span>
                <span className="rrugc-progress"><i style={{ width: progress + "%" }} /><small>{progressBase}/{item.target_count}</small></span>
              </button>;
            })}
          </div>}
        </section>
      </div>

      {selected && <section className="rrugc-card rrugc-live">
        <div className="rrugc-section-heading">
          <div><small>LIVE SCAN</small><h2>{selected.name}</h2><p>{selected.query}</p></div>
          <div className="rrugc-live-meta"><span>Scout: <b>{selected.scout_status}</b></span><span>Last seen: <b>{time(selected.scout_last_seen_at)}</b></span><span>Drive: <b>{selected.drive_ready}</b></span></div>
        </div>
        {candidates.length === 0 ? <p className="rrugc-empty">Start the Browser Scout command to collect Pinterest candidates.</p> : <div className="rrugc-grid">
          {candidates.map(candidate => <article key={candidate.id} className="rrugc-candidate">
            <a href={candidate.pin_url} target="_blank" rel="noreferrer"><img src={candidate.image_url} alt={candidate.alt_text || "Pinterest reference candidate"} loading="lazy" referrerPolicy="no-referrer" /></a>
            <div><strong>{statusLabel[candidate.status]}</strong><span>{candidate.width && candidate.height ? candidate.width + "×" + candidate.height : candidate.alt_text || "Pinterest candidate"}</span></div>
            <footer>
              {candidate.web_url ? <a href={candidate.web_url} target="_blank" rel="noreferrer">Open in Drive</a> : <button type="button" disabled={Boolean(importingId) || candidate.status === "importing"} onClick={() => void importOne(candidate)}>{importingId === candidate.id ? "Importing…" : "Save to Drive"}</button>}
            </footer>
          </article>)}
        </div>}
      </section>}
      </div>
    </section>
  </main>;
}
