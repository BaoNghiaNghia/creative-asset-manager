import { useEffect, useMemo, useRef, useState } from "react";
import { createKeywordImage, listKeywordImages, listStage2Skills, queueAllKeywordImages, retryKeywordImage } from "./api";
import { RrugcStageHeader } from "./RrugcStageHeader";
import type { KeywordImagePage, KeywordImageRow, KeywordImageStatus, Stage2Skill, Stage2SkillCatalog, Stage2SkillSelection } from "./types";
import "./KeywordImageStage.css";

const PAGE_SIZES = [20, 50, 100] as const;
const STATUS_LABEL: Record<KeywordImageStatus, string> = {
  not_run: "Not run", queued: "Queued", running: "Running", completed: "Completed", failed: "Failed",
};
const STATUS_ORDER: KeywordImageStatus[] = ["not_run", "queued", "running", "completed", "failed"];
const EMPTY: KeywordImagePage = {
  items: [], total: 0, page: 1, page_size: 20,
  overview: { not_run: 0, queued: 0, running: 0, completed: 0, failed: 0 },
};

function skillKey(skill: Pick<Stage2Skill, "source" | "skill_id" | "skill_name">): string {
  return [skill.source, skill.skill_id || skill.skill_name].join(":");
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
  const [allQueued, setAllQueued] = useState(0);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [catalog, setCatalog] = useState<Stage2SkillCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [selectedSkillKey, setSelectedSkillKey] = useState("");
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
        if (!controller.signal.aborted) setCatalog(value);
      })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load Skills.");
      })
      .finally(() => { if (!controller.signal.aborted) setCatalogLoading(false); });
    return () => controller.abort();
  }, [active, skillCatalogRevision]);

  const readySkills = useMemo(() => (catalog?.items || []).filter(item => item.ready), [catalog]);
  useEffect(() => {
    if (readySkills.length === 0) return;
    if (!readySkills.some(item => skillKey(item) === selectedSkillKey)) {
      setSelectedSkillKey(skillKey(readySkills[0]));
    }
  }, [readySkills, selectedSkillKey]);
  const selectedSkill = readySkills.find(item => skillKey(item) === selectedSkillKey) || null;
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
      if (row.status === "failed") {
        await retryKeywordImage(row.keyword_id);
        setMessage("Retry queued for " + row.keyword + ".");
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
      if (!selectedSkillInput) setError("Select a ready Skill before generating.");
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

  const totalUsed = Object.values(data.overview).reduce((sum, count) => sum + count, 0);
  const first = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const last = Math.min(data.total, data.page * data.page_size);
  const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
  return <div className="rrugc-keyword-gen">
    <RrugcStageHeader
      kicker="STAGE 1 / KEYWORD-TO-IMAGE"
      title="Generate images from used keywords"
      description="Only keywords marked Used in Stage 0 enter this queue. Select a Skill, generate in bulk, track every output, and retry failed jobs without losing completed images."
      actions={<div className="rrugc-keyword-gen-actions">
        <button type="button" className="rrugc-global-management-button" onClick={onManageSkills}>Manage skills</button>
        {bulkRunning
          ? <button type="button" className="rrugc-keyword-gen-stop" onClick={() => bulkAbortRef.current?.abort()}>Stop queueing ({allQueued})</button>
          : <button type="button" className="rrugc-keyword-gen-primary" onClick={() => void queueAll()} disabled={!selectedSkill || catalogLoading}>Generate all unused</button>}
      </div>}
    />
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
      <button type="button" className="rrugc-keyword-gen-refresh" onClick={() => { setLoading(true); void refresh().catch(reason => { setLoading(false); setError(String(reason)); }); }} aria-label="Refresh keyword jobs" title="Refresh">↻</button>
    </div>
    {error && <p className="rrugc-keyword-gen-error" role="alert">{error}</p>}
    {message && <p className="rrugc-keyword-gen-message" role="status">{message}</p>}
    {catalog && !readySkills.length && !catalogLoading && <p className="rrugc-keyword-gen-hint">No synced Skill is ready. Upload or sync one through Manage skills to begin generation.</p>}
    <div className="rrugc-keyword-gen-table-wrap" aria-busy={loading}>
      <table className="rrugc-keyword-gen-table">
        <thead><tr><th>Keyword / Source</th><th>Volume</th><th>Skill</th><th>Status</th><th>Output</th><th>Action</th></tr></thead>
        <tbody>
          {loading && !data.items.length
            ? Array.from({ length: 6 }, (_, index) => <tr key={index} className="rrugc-keyword-gen-loading"><td colSpan={6}><span /></td></tr>)
            : data.items.map(row => {
              const busy = workingIds.has(row.keyword_id);
              return <tr key={row.keyword_id}>
                <td><div className="rrugc-keyword-gen-keyword">
                  <div className="rrugc-keyword-gen-source">
                    {row.source_image_url ? <img src={row.source_image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span aria-hidden="true">Aa</span>}
                  </div>
                  <div><strong title={row.keyword}>{row.keyword}</strong><small>Used · Stage 0</small></div>
                </div></td>
                <td className="rrugc-keyword-gen-volume">{row.search_volume.toLocaleString("en-US")}</td>
                <td><span className="rrugc-keyword-gen-skill-name" title={row.skill_name || selectedSkill?.display_name || ""}>{row.skill_name || selectedSkill?.display_name || "—"}</span></td>
                <td><span className={"rrugc-keyword-gen-badge status-" + row.status}><i />{STATUS_LABEL[row.status]}</span>{row.status === "failed" && <small className="rrugc-keyword-gen-failure" title={row.error_message || row.error_code || ""}>{row.error_message || row.error_code || "Generation failed"}</small>}{row.status === "running" || row.status === "queued" ? <small className="rrugc-keyword-gen-attempt">Attempt {row.attempt_count}/{row.max_attempts}</small> : null}</td>
                <td>{row.output_url
                  ? <a href={row.output_url} target="_blank" rel="noreferrer" className="rrugc-keyword-gen-output" aria-label={"View generated output for " + row.keyword}><img src={row.output_url + "?thumbnail=true"} alt={"Generated " + row.keyword} loading="lazy" /><span>View output ↗</span></a>
                  : <span className="rrugc-keyword-gen-no-output">No output yet</span>}</td>
                <td>
                  {row.status === "not_run" && <button type="button" className="rrugc-keyword-gen-row-action" disabled={busy || !selectedSkill} onClick={() => void runOne(row)}>{busy ? "Queueing…" : "Generate"}</button>}
                  {row.status === "failed" && <button type="button" className="rrugc-keyword-gen-row-action retry" disabled={busy || row.retry_count >= 3} onClick={() => void runOne(row)}>{busy ? "Retrying…" : row.retry_count >= 3 ? "Retry limit" : "Retry"}</button>}
                  {row.status === "queued" && <span className="rrugc-keyword-gen-muted">Waiting</span>}
                  {row.status === "running" && <span className="rrugc-keyword-gen-muted">In progress</span>}
                  {row.status === "completed" && <span className="rrugc-keyword-gen-done">Completed</span>}
                </td>
              </tr>;
            })}
          {!loading && data.items.length === 0 && <tr><td colSpan={6}><div className="rrugc-keyword-gen-empty"><strong>No keywords in this view</strong><p>Mark keywords as Used in Stage 0, or clear the current search/status filter.</p></div></td></tr>}
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
  </div>;
}
