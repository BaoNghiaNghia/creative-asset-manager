import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { createKeywordImage, createManualKeywordImage, getKeywordAnalysisDetail, getKeywordImageJobStatus, listKeywordImages, listStage2Skills, queueAllKeywordImages, regenerateKeywordImage } from "./api";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { ActionMessageToast } from "../components/ActionToast";
import { RrugcActionIcon } from "./RrugcActionIcon";
import { SkillJobLogDialog } from "./SkillJobLogDialog";
import { GenerationOutputVersionsDialog } from "./GenerationOutputVersionsDialog";
import { KeywordImageOutputSlider } from "./KeywordImageOutputSlider";
import { KeywordDetailModal } from "./KeywordAnalysisTable";
import type { KeywordImagePage, KeywordImageRow, KeywordImageStatus, KeywordVolume, Stage2Skill, Stage2SkillCatalog, Stage2SkillSelection } from "./types";
import "./KeywordImageStage.css";

const PAGE_SIZES = [20, 50, 100] as const;

export function formatKeywordJobDuration(startedAt: string | null | undefined, finishedAt: string | null | undefined, now = Date.now()): string {
  if (!startedAt) return "—";
  const start = Date.parse(startedAt);
  const end = finishedAt ? Date.parse(finishedAt) : now;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const seconds = Math.floor((end - start) / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const rest = seconds % 60;
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}

function formatKeywordJobFinishedAt(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}
const STATUS_LABEL: Record<KeywordImageStatus, string> = {
  not_run: "Not run", queued: "Queued", running: "Running", completed: "Completed", failed: "Failed",
};
const STATUS_ORDER: KeywordImageStatus[] = ["not_run", "queued", "running", "completed", "failed"];
const EMPTY: KeywordImagePage = {
  items: [], total: 0, page: 1, page_size: 20,
  overview: { not_run: 0, queued: 0, running: 0, completed: 0, failed: 0 },
};

export function isKeywordArtworkSkill(skill: Stage2Skill): boolean {
  return skill.ready && skill.keyword_artwork_ready;
}

function skillKey(skill: Pick<Stage2Skill, "source" | "skill_id" | "skill_name">): string {
  return [skill.source, skill.skill_id || skill.skill_name].join(":");
}

type KeywordActionIcon = "generate" | "regenerate" | "retry" | "versions" | "logs";

function KeywordActionGlyph({ kind }: { kind: KeywordActionIcon }) {
  if (kind === "logs") return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 3h10l3 3v15H4V3h3Z"/><path d="M8 10h8M8 14h8M8 18h5"/></svg>;
  if (kind === "versions") return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="7" y="7" width="13" height="13" rx="2"/><path d="M4 16V5a2 2 0 0 1 2-2h11"/></svg>;
  if (kind === "generate") return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m12 3 1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6L12 3ZM19 17v4m-2-2h4"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 0-2.5 6"/><path d="M20 4v7h-7"/></svg>;
}

export function KeywordImageRowControls({
  row, busy, canGenerate, onRun, onLogs, showPreview = true,
}: {
  row: KeywordImageRow;
  busy: boolean;
  canGenerate: boolean;
  onRun: () => void;
  onLogs: () => void;
  showPreview?: boolean;
}) {
  const [confirmRegenerate, setConfirmRegenerate] = useState(false);
  const regenerateButtonRef = useRef<HTMLButtonElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const acceptButtonRef = useRef<HTMLButtonElement>(null);
  const regenerate = row.status === "completed";
  const generate = row.status === "not_run";
  const runnable = regenerate || generate;
  const runLabel = regenerate ? "Regenerate" : "Redesign Qoutes";
  const runDisabled = busy || (generate && !canGenerate);
  const runTitle = generate && !canGenerate ? "Choose an enabled generation Skill" : busy ? "Queueing…" : regenerate ? "Regenerate image" : "Redesign Qoutes";

  useEffect(() => {
    if (!confirmRegenerate) return;
    cancelButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setConfirmRegenerate(false);
      } else if (event.key === "Tab") {
        if (event.shiftKey && document.activeElement === cancelButtonRef.current) {
          event.preventDefault();
          acceptButtonRef.current?.focus();
        } else if (!event.shiftKey && document.activeElement === acceptButtonRef.current) {
          event.preventDefault();
          cancelButtonRef.current?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      regenerateButtonRef.current?.focus();
    };
  }, [confirmRegenerate]);

  return <div className="rrugc-keyword-result-cell" aria-busy={busy}>
    {showPreview && <div className="rrugc-keyword-result-media">
    {row.output_url
      ? <a className="rrugc-keyword-result-preview is-available" href={row.output_url} target="_blank" rel="noreferrer"
          title={"View output for " + row.keyword} aria-label={"View generated output for " + row.keyword}>
          <img src={row.output_url + "?thumbnail=true"} alt={row.keyword + " generated output"} loading="lazy" />
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 5h5v5M19 5l-8 8"/></svg>
        </a>
      : <span className="rrugc-keyword-result-preview is-empty" role="img" aria-label="No generated output yet" title="No output yet">
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m4 17 5-5 3.5 3.5 2.5-2.5 4 4"/></svg>
        </span>}
    <span className="rrugc-keyword-result-caption">{row.saved_output_count > 0
      ? `${row.saved_output_count.toLocaleString()} image${row.saved_output_count === 1 ? "" : "s"} saved${row.status === "failed" ? " · partial" : ""}`
      : row.output_url ? "Latest output" : row.status === "running" ? "Generating…" : row.status === "queued" ? "Waiting…" : "No output"}</span>
    </div>}
    <div className="rrugc-keyword-result-actions" role="group" aria-label={"Actions for " + row.keyword}>
      {runnable && <button type="button" className={"rrugc-keyword-result-action is-" + (regenerate ? "regenerate" : "generate")}
        ref={regenerateButtonRef} onClick={() => regenerate ? setConfirmRegenerate(true) : onRun()}
        disabled={runDisabled} title={runTitle} aria-label={runLabel + " " + row.keyword}>
        <KeywordActionGlyph kind={regenerate ? "regenerate" : "generate"} />
        <span className="rrugc-keyword-action-label">{runLabel}</span>
      </button>}
      {row.job_id && <button type="button" className="rrugc-keyword-result-action"
        onClick={onLogs} title="View job logs" aria-label={"View logs for " + row.keyword}>
        <KeywordActionGlyph kind="logs" />
        <span className="rrugc-keyword-action-label">Logs</span>
      </button>}
      {(row.status === "running" || row.status === "queued") && <span className={"rrugc-keyword-result-pending status-" + row.status} title={row.status === "queued" ? "Waiting in the queue" : "Generation in progress"}>
        <span className="rrugc-keyword-pending-dot" />
        {row.status === "queued" ? "In queue" : "Processing"}
      </span>}
    </div>
    {confirmRegenerate && regenerate && createPortal(
      <div className="rrugc-keyword-regenerate-backdrop" onMouseDown={event => {
        if (event.target === event.currentTarget) setConfirmRegenerate(false);
      }}>
        <section className="rrugc-keyword-regenerate-dialog" role="dialog" aria-modal="true"
          aria-labelledby="rrugc-regenerate-title" aria-describedby="rrugc-regenerate-description">
          <div className="rrugc-keyword-regenerate-icon"><KeywordActionGlyph kind="regenerate" /></div>
          <h2 id="rrugc-regenerate-title">Regenerate image?</h2>
          <p id="rrugc-regenerate-description">Queue a new version for <strong>{row.keyword}</strong>? Your previous images will be kept.</p>
          <div className="rrugc-keyword-regenerate-buttons">
            <button ref={cancelButtonRef} type="button" onClick={() => setConfirmRegenerate(false)}>Cancel</button>
            <button ref={acceptButtonRef} type="button" className="is-accept" onClick={() => {
              setConfirmRegenerate(false);
              onRun();
            }}>Accept</button>
          </div>
        </section>
      </div>, document.body,
    )}
  </div>;
}

export function KeywordImageStage({
  active,
  skillCatalogRevision,
  onManageSkills,
}: {
  active: boolean;
  skillCatalogRevision: number;
  onManageSkills: () => void;
}) {
  const [data, setData] = useState<KeywordImagePage>(EMPTY);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [status, setStatus] = useState<"all" | KeywordImageStatus>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [workingIds, setWorkingIds] = useState<Set<string>>(new Set());
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [versionsJob, setVersionsJob] = useState<{ jobId: string; title: string; initialVersion?: number } | null>(null);
  const [keywordDetailTarget, setKeywordDetailTarget] = useState<{ id: string; keyword: string } | null>(null);
  const [keywordDetail, setKeywordDetail] = useState<KeywordVolume | null>(null);
  const [keywordDetailLoading, setKeywordDetailLoading] = useState(false);
  const [keywordDetailError, setKeywordDetailError] = useState("");
  const [allQueued, setAllQueued] = useState(0);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [catalog, setCatalog] = useState<Stage2SkillCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [selectedSkillKey, setSelectedSkillKey] = useState("");
  const [manualKeyword, setManualKeyword] = useState("");
  const [manualSubmitting, setManualSubmitting] = useState(false);
  const [manualResult, setManualResult] = useState<KeywordImageRow | null>(null);
  const [manualFocus, setManualFocus] = useState<{ keywordId: string; jobId: string; keyword: string } | null>(null);
  const bulkAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => { setDebouncedQuery(query.trim()); setPage(1); }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setCatalogLoading(true);
    void listStage2Skills(false, controller.signal)
      .then(value => {
        if (!controller.signal.aborted) {
          setCatalog(value);
          const preferred = value.items.find(item => skillKey(item) === value.stage_defaults?.stage1 && isKeywordArtworkSkill(item))
            || value.items.find(item => isKeywordArtworkSkill(item));
          if (preferred) setSelectedSkillKey(skillKey(preferred));
        }
      })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load Skills.");
      })
      .finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => controller.abort();
  }, [active, skillCatalogRevision]);

  // Respect the stage default; any ready image-generation Skill can receive
  // Stage 1's keyword-only prompt, including skills with bundled references.
  const readySkills = useMemo(() => (catalog?.items || [])
    .filter(item => isKeywordArtworkSkill(item)), [catalog]);
  useEffect(() => {
    if (readySkills.length === 0) return;
    if (!readySkills.some(item => skillKey(item) === selectedSkillKey)) {
      setSelectedSkillKey(skillKey(readySkills[0]));
    }
  }, [readySkills, selectedSkillKey]);
  const selectedSkill = readySkills.find(item => skillKey(item) === selectedSkillKey) || null;
  const isSixDesignSkill = selectedSkill?.skill_name === "gatorhats-stage1-six-designs";
  const selectedSkillInput: Stage2SkillSelection | null = selectedSkill ? {
    source: selectedSkill.source, skill_id: selectedSkill.skill_id,
    skill_name: selectedSkill.skill_name,
    skill_version: selectedSkill.synced_version || selectedSkill.default_version || selectedSkill.local_version || null,
  } : null;

  const refresh = (signal?: AbortSignal) => listKeywordImages(
    { page, pageSize, query: debouncedQuery, status }, signal,
  ).then(value => {
    if (!signal?.aborted) {
      setData(value);
      setLoading(false);
    }
  });

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let busy = true;
    setLoading(true);
    void refresh(controller.signal)
      .catch(reason => { if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Could not load Stage 1."); setLoading(false); } })
      .finally(() => { busy = false; });
    const timer = window.setInterval(() => {
      if (document.hidden || busy) return;
      busy = true;
      void refresh(controller.signal).catch(() => undefined).finally(() => { busy = false; });
    }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [active, page, pageSize, debouncedQuery, status]);

  useEffect(() => {
    if (!active || !manualFocus) return;
    const controller = new AbortController();
    const poll = () => {
      if (controller.signal.aborted) return;
      void getKeywordImageJobStatus(manualFocus.jobId, controller.signal)
        .then(job => { if (!controller.signal.aborted) setManualResult(job); })
        .catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(() => {
      if (!document.hidden) poll();
    }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [active, manualFocus]);

  useEffect(() => {
    if (!keywordDetailTarget) return;
    const controller = new AbortController();
    setKeywordDetail(null);
    setKeywordDetailError("");
    setKeywordDetailLoading(true);
    void getKeywordAnalysisDetail(keywordDetailTarget.id, controller.signal)
      .then(item => { if (!controller.signal.aborted) setKeywordDetail(item); })
      .catch(reason => {
        if (!controller.signal.aborted) setKeywordDetailError(reason instanceof Error ? reason.message : "Could not load keyword details.");
      })
      .finally(() => { if (!controller.signal.aborted) setKeywordDetailLoading(false); });
    return () => controller.abort();
  }, [keywordDetailTarget]);

  // Stopping a batch stops only future queue requests; durable jobs already queued continue.
  useEffect(() => () => bulkAbortRef.current?.abort(), []);

  const runOne = async (row: KeywordImageRow) => {
    if (workingIds.has(row.keyword_id)) return;
    if (row.status === "not_run" && !selectedSkillInput) {
      setError("Select a synced, ready Skill before generating.");
      return;
    }
    setWorkingIds(current => new Set(current).add(row.keyword_id));
    setError("");
    setMessage("");
    try {
      if (row.status === "completed") {
        await regenerateKeywordImage(row.keyword_id, selectedSkillInput);
        setMessage("New output version queued for " + row.keyword + ". Previous output stays available.");
      } else if (row.status === "not_run" && selectedSkillInput) {
        const result = await createKeywordImage(row.keyword_id, selectedSkillInput);
        setMessage((result.created ? "Generation queued" : "Job already exists") + " for " + row.keyword + ".");
      }
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue generation.");
    } finally {
      setWorkingIds(current => { const next = new Set(current); next.delete(row.keyword_id); return next; });
    }
  };

  const queueAll = async () => {
    if (!selectedSkillInput || bulkRunning) {
      if (!selectedSkillInput) setError("Choose an enabled image-generation Skill before generating.");
      return;
    }
    const controller = new AbortController();
    bulkAbortRef.current = controller;
    setBulkRunning(true);
    setAllQueued(0);
    setError("");
    setMessage("");
    let total = 0;
    try {
      // Server batches at most 50 rows and idempotently excludes existing jobs.
      // Repeat until every currently Used keyword has a durable queued job.
      while (!controller.signal.aborted) {
        const result = await queueAllKeywordImages(selectedSkillInput, controller.signal);
        total += result.queued;
        setAllQueued(total);
        if (!result.remaining || !result.queued) break;
      }
      setMessage(controller.signal.aborted
        ? "Stopped queueing. " + total + " jobs already queued will continue running."
        : total + " keyword generation" + (total === 1 ? "" : "s") + " queued. Skills run asynchronously on the worker.");
      if (active) await refresh();
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Batch queueing failed.");
    } finally {
      if (bulkAbortRef.current === controller) bulkAbortRef.current = null;
      setBulkRunning(false);
    }
  };

  const submitManual = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = manualKeyword.trim().split(/\s+/).join(" ");
    if (!value || !selectedSkillInput || manualSubmitting) return;
    setManualSubmitting(true);
    setError("");
    setMessage("");
    try {
      const result = await createManualKeywordImage(value, selectedSkillInput);
      setManualResult(null);
      setManualFocus({ keywordId: result.keyword_id, jobId: result.job_id, keyword: value });
      setStatus("all");
      setQuery(value);
      setPage(1);
      setMessage("Generation queued for " + value + ". Follow the job status and review saved outputs when ready.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not generate from the entered keyword.");
    } finally {
      setManualSubmitting(false);
    }
  };
  const totalUsed = Object.values(data.overview).reduce((sum, count) => sum + count, 0);
  const first = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const last = Math.min(data.total, data.page * data.page_size);
  const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
  return <div className="rrugc-keyword-gen">
    <RrugcStageHeader
      kicker="STAGE 1 / KEYWORD-TO-IMAGE"
      title="Generate images from used keywords"
      description="Generate six separate final embroidery concepts per keyword, track progress, and compare previous output versions."
      actions={<div className="rrugc-keyword-gen-actions">
        <button type="button" className="rrugc-global-management-button rrugc-icon-action" onClick={onManageSkills}><RrugcActionIcon name="skills" />Manage skills</button>
        {bulkRunning
          ? <button type="button" className="rrugc-keyword-gen-stop rrugc-icon-action" onClick={() => bulkAbortRef.current?.abort()}><RrugcActionIcon name="stop" />Stop queueing ({allQueued})</button>
          : <button type="button" className="rrugc-keyword-gen-primary rrugc-icon-action" onClick={() => void queueAll()} disabled={!selectedSkill || catalogLoading}><RrugcActionIcon name="sparkles" />Generate all unused</button>}
      </div>}
    />
    <form className="rrugc-keyword-manual" onSubmit={event => void submitManual(event)}>
      <div className="rrugc-keyword-manual-heading">
        <strong>Create artwork from a keyword</strong>
        <span>{isSixDesignSkill ? "6 separate high-resolution final designs · intermediate drafts excluded" : "Stage 1 requires the Six Designs Skill"}</span>
      </div>
      <div className="rrugc-keyword-manual-controls">
        <label htmlFor="rrugc-stage1-manual-keyword" className="sr-only">Enter keyword or saying</label>
        <input id="rrugc-stage1-manual-keyword" value={manualKeyword} maxLength={150}
          onChange={event => setManualKeyword(event.target.value)}
          placeholder="Enter keyword or quote, e.g. BEACH PLEASE" />
        <button type="submit" className="rrugc-icon-action" disabled={!manualKeyword.trim() || !selectedSkillInput || manualSubmitting}>
          <RrugcActionIcon name={manualSubmitting ? "refresh" : "sparkles"} />{manualSubmitting ? "Queueing…" : "Generate"}
        </button>
      </div>
      {manualFocus && <div className="rrugc-keyword-manual-result" aria-live="polite">
        <div className="rrugc-keyword-manual-result-title">
          <strong title={manualFocus.keyword}>{manualFocus.keyword}</strong>
          <small>{manualResult ? STATUS_LABEL[manualResult.status] : "Queued · waiting for job"}</small>
        </div>
        {manualResult
          ? <KeywordImageRowControls row={manualResult} busy={workingIds.has(manualResult.keyword_id)}
              canGenerate={Boolean(selectedSkill)}
              onRun={() => void runOne(manualResult)}
              onLogs={() => { if (manualResult.job_id) setLogJobId(manualResult.job_id); }} />
          : <span className="rrugc-keyword-manual-pending">Waiting for job…</span>}
        {manualResult?.status === "completed" && manualResult.output_url
          && <a className="rrugc-keyword-manual-final" href={manualResult.output_url} target="_blank" rel="noreferrer">View latest output ↗</a>}
      </div>}
    </form>
    <div className="rrugc-keyword-gen-stats" aria-label="Stage 1 generation status">
      <button type="button" aria-pressed={status === "all"} className={status === "all" ? "active" : ""} onClick={() => { setStatus("all"); setPage(1); }}>
        <strong>{totalUsed.toLocaleString()}</strong><span>All used</span>
      </button>
      {STATUS_ORDER.map(key => <button key={key} type="button" aria-pressed={status === key} className={status === key ? "active" : ""} onClick={() => { setStatus(key); setPage(1); }}>
        <strong>{(data.overview[key] || 0).toLocaleString()}</strong><span>{STATUS_LABEL[key]}</span>
      </button>)}
    </div>
    <div className="rrugc-keyword-gen-toolbar">
      <label className="rrugc-keyword-gen-search">
        <span className="sr-only">Search used keywords</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/></svg>
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search used keywords…" />
      </label>
      <label className="rrugc-keyword-gen-skill">
        <span>Generation Skill</span>
        <select value={selectedSkillKey} onChange={event => setSelectedSkillKey(event.target.value)} disabled={catalogLoading || !readySkills.length} aria-label="Keyword generation Skill">
          {!readySkills.length && <option value="">{catalogLoading ? "Loading Skills…" : "No ready Skills"}</option>}
          {readySkills.map(skill => <option key={skillKey(skill)} value={skillKey(skill)}>{skill.display_name || skill.skill_name}{skill.synced_version ? " · " + skill.synced_version : ""}</option>)}
        </select>
      </label>
      <button type="button" className="rrugc-keyword-gen-refresh" onClick={() => { setLoading(true); void refresh().catch(reason => { setLoading(false); setError(String(reason)); }); }} aria-label="Refresh keyword jobs" title="Refresh jobs" disabled={loading}>
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 1-2.4-5.7" /><path d="M20 4v7h-7" /></svg>
      </button>
    </div>
    <ActionMessageToast message={error} tone="error" />
    <ActionMessageToast message={message} />
    {catalog && !readySkills.length && !catalogLoading && <p className="rrugc-keyword-gen-hint" role="alert">No enabled image-generation Skills are available. Install or enable a Skill in Manage Skills.</p>}
    <div className="rrugc-keyword-gen-list-heading">
      <div className="rrugc-keyword-gen-list-title">
        <strong>Keyword jobs</strong>
        <span>6 final images per job · draft previews saved as compact WebP in <a href="https://drive.google.com/drive/folders/1HNV_9BbJohoB5hKsGxpj8owNF7jG-5SB" target="_blank" rel="noreferrer">Drive Temp <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M13 5h6v6m0-6-9 9"/><path d="M19 13v6H5V5h6"/></svg></a> · automatically deleted after 48h. Failed jobs stop after one attempt.</span>
      </div>
      <span className="rrugc-keyword-gen-list-count">{first}–{last} of {data.total.toLocaleString()} jobs</span>
    </div>
    <div className="rrugc-keyword-gen-table-wrap" aria-busy={loading}>
      <table className="rrugc-keyword-gen-table">
        <thead><tr><th>Keyword / Source</th><th>Search volume</th><th>Generation skill</th><th>Job status</th><th>Duration</th><th>Finished at</th><th>Output</th><th>Actions</th></tr></thead>
        <tbody>
          {loading && !data.items.length
            ? Array.from({ length: 6 }, (_, index) => <tr key={index} className="rrugc-keyword-gen-loading"><td colSpan={8}><span /></td></tr>)
            : data.items.map(row => {
              const busy = workingIds.has(row.keyword_id);
              return <tr key={row.keyword_id} className={"rrugc-keyword-row status-" + row.status}>
                <td><button type="button" className="rrugc-keyword-gen-keyword"
                  onClick={() => setKeywordDetailTarget({ id: row.keyword_id, keyword: row.keyword })}
                  aria-label={"Open keyword details for " + row.keyword} title="View Stage 0 Google Ads keyword details">
                  <div className="rrugc-keyword-gen-source">
                    {row.source_image_url ? <img src={row.source_image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span aria-hidden="true">Aa</span>}
                  </div>
                  <div><strong title={row.keyword}>{row.keyword}</strong><small>View Stage 0 details ↗</small></div>
                </button></td>
                <td className="rrugc-keyword-gen-volume">{row.search_volume.toLocaleString("en-US")}</td>
                <td><div className="rrugc-keyword-gen-skill-cell">
                  <span className="rrugc-keyword-gen-skill-name" title={row.skill_name || selectedSkill?.display_name || ""}>{row.skill_name || selectedSkill?.display_name || "Choose Skill"}</span>
                  {row.status === "not_run" ? <small>Next run</small> : row.skill_version ? <small>v{row.skill_version}</small> : null}
                </div></td>
                <td><div className="rrugc-keyword-gen-status-cell">
                  <span className={"rrugc-keyword-gen-badge status-" + row.status}><i />{STATUS_LABEL[row.status]}</span>
                  {row.status === "failed" && <small className="rrugc-keyword-gen-failure" title={row.error_message || row.error_code || ""}>{row.error_message || row.error_code || "Generation failed"}</small>}
                  {row.status === "running" || row.status === "queued" ? <small className="rrugc-keyword-gen-attempt">{row.status === "queued" ? "Waiting for worker" : "Attempt " + Math.max(1, row.attempt_count) + " of " + row.max_attempts}</small> : null}
                  {row.status === "not_run" && <small className="rrugc-keyword-gen-attempt">Ready to generate</small>}
                </div></td>
                <td className="rrugc-keyword-gen-duration" title={row.started_at || "Job has not started"}>{row.started_at
                  ? formatKeywordJobDuration(row.started_at, row.finished_at) : "—"}{row.started_at && !row.finished_at && row.status === "running" && <small>Running</small>}</td>
                <td className="rrugc-keyword-gen-finished">{row.finished_at
                  ? <time dateTime={row.finished_at}>{formatKeywordJobFinishedAt(row.finished_at)}</time>
                  : <span className="rrugc-keyword-gen-muted">{row.status === "running" ? "In progress" : row.status === "queued" ? "In queue" : "—"}</span>}</td>
                <td className="rrugc-keyword-gen-output-column"><KeywordImageOutputSlider row={row}
                  onOpenVersion={version => { if (row.job_id) setVersionsJob({ jobId: row.job_id, title: row.keyword, initialVersion: version }); }} /></td>
                <td className="rrugc-keyword-gen-result">
                  <KeywordImageRowControls row={row} busy={busy} canGenerate={Boolean(selectedSkill)} showPreview={false}
                    onRun={() => void runOne(row)}
                    onLogs={() => { if (row.job_id) setLogJobId(row.job_id); }} />
                </td>
              </tr>;
            })}
          {!loading && data.items.length === 0 && <tr><td colSpan={8}><div className="rrugc-keyword-gen-empty"><strong>No keywords in this view</strong><p>Mark keywords as Used in Stage 0, or clear the current search/status filter.</p></div></td></tr>}
        </tbody>
      </table>
    </div>
    <footer className="rrugc-keyword-gen-footer">
      <span>{first}–{last} of {data.total.toLocaleString()} keywords</span>
      <div className="rrugc-keyword-gen-pages">
        <button type="button" onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1 || loading} aria-label="Previous Stage 1 page">‹</button>
        <span>Page {data.page} / {pageCount}</span>
        <button type="button" onClick={() => setPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount || loading} aria-label="Next Stage 1 page">›</button>
        <label>Rows <select value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setPage(1); }} aria-label="Stage 1 rows per page">{PAGE_SIZES.map(size => <option key={size}>{size}</option>)}</select></label>
      </div>
    </footer>
    {logJobId && <SkillJobLogDialog stage="stage1" jobId={logJobId} onClose={() => setLogJobId(null)} />}
    {versionsJob && <GenerationOutputVersionsDialog stage="stage1" jobId={versionsJob.jobId} title={versionsJob.title}
      initialVersion={versionsJob.initialVersion} onClose={() => setVersionsJob(null)} />}
    {keywordDetailTarget && (keywordDetail
      ? <KeywordDetailModal item={keywordDetail} onClose={() => setKeywordDetailTarget(null)} />
      : <div className="rrugc-stage0-detail-backdrop" onMouseDown={event => {
          if (event.target === event.currentTarget) setKeywordDetailTarget(null);
        }}>
          <section role="dialog" aria-modal="true" aria-label={"Keyword details for " + keywordDetailTarget.keyword}
            className="rrugc-stage0-detail-modal rrugc-stage1-detail-loading">
            <header className="rrugc-stage0-detail-header">
              <div><small>KEYWORD DETAIL · GOOGLE ADS</small><h2>{keywordDetailTarget.keyword}</h2></div>
              <button type="button" className="rrugc-stage0-detail-close" aria-label="Close keyword details"
                onClick={() => setKeywordDetailTarget(null)}>×</button>
            </header>
            <div className="rrugc-stage1-detail-placeholder" role={keywordDetailError ? "alert" : "status"}>
              {keywordDetailError || (keywordDetailLoading ? "Loading Stage 0 Google Ads and trademark details…" : "Loading keyword details…")}
            </div>
          </section>
        </div>)}
  </div>;
}