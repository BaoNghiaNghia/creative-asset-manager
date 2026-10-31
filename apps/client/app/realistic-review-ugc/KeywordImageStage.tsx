import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { backfillKeywordCreativeBriefs, getKeywordAnalysisDetail, getCreativeV3GenerationStatus, listKeywordImages, publishAvailableKeywordImages } from "./api";
import { queueStage1CreativeBriefRun, type Stage1ImageAction } from "./stage1CreativeBriefRun";
import { getTypographyStatus, listTypographyReferences } from "./typography/api";
import { formatRelativeDateTime } from "./RelativeDateTime";
import { TypographyIdeasPage } from "./typography/TypographyIdeasPage";
import { Stage1Subnav, type TypographyStageView } from "./typography/Stage1Subnav";
import { TypographyStyleLab, type StyleLabKeyword } from "./typography/TypographyStyleLab";
import type { TypographyReferencePage } from "./typography/types";
import { RrugcStageHeader } from "./RrugcStageHeader";
import { SortDirectionIndicator } from "./SortDirectionIndicator";
import "./SortColumnHighlight.css";
import { ActionMessageToast } from "../components/ActionToast";
import { RrugcActionIcon } from "./RrugcActionIcon";
import { SkillJobLogDialog } from "./SkillJobLogDialog";
import { GenerationOutputVersionsDialog } from "./GenerationOutputVersionsDialog";
import { KeywordImageOutputSlider } from "./KeywordImageOutputSlider";
import { PickedDesignModal } from "./PickedDesignModal";
import { CreativeBriefDialog } from "./CreativeBriefDialog";
import { KeywordDetailModal, KeywordTrendChart } from "./KeywordAnalysisTable";
import type { KeywordImagePage, KeywordImageRow, KeywordImageStatus, KeywordVolume } from "./types";
import "./KeywordImageStage.css";

const PAGE_SIZES = [20, 50, 100] as const;
export type KeywordImageSortBy = "keyword" | "search_volume" | "picked_at" | "duration" | "finished_at";
type KeywordImageSortDirection = "asc" | "desc";

function Stage1SortHeader({ column, label, sortBy, sortDirection, onSort }: {
  column: KeywordImageSortBy;
  label: string;
  sortBy: KeywordImageSortBy;
  sortDirection: KeywordImageSortDirection;
  onSort: (column: KeywordImageSortBy) => void;
}) {
  const active = sortBy === column;
  return <th aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}>
    <button type="button" className={"rrugc-keyword-gen-sort" + (active ? " active rrugc-sort-" + sortDirection : "")}
      onClick={() => onSort(column)} aria-label={`Sort by ${label} ${active ? (sortDirection === "asc" ? "descending" : "ascending") : (column === "keyword" ? "ascending" : "descending")}`}>
      <span>{label}</span><SortDirectionIndicator active={active} direction={sortDirection} />
    </button>
  </th>;
}

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

function formatKeywordJobFinishedAtAbsolute(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function formatStage0PickedAt(value: string | null | undefined, now = Date.now()): string {
  return formatRelativeDateTime(value, now);
}

export function formatKeywordJobFinishedAt(value: string | null | undefined, now = Date.now()): string {
  return formatRelativeDateTime(value, now);
}
const STATUS_LABEL: Record<KeywordImageStatus, string> = {
  not_run: "Not run", queued: "Queued", running: "Running", completed: "Completed", failed: "Failed",
};
const STATUS_ORDER: KeywordImageStatus[] = ["not_run", "queued", "running", "completed", "failed"];
/**
 * Stage 1 status details are intentionally on-demand. Keeping error text in a
 * body-level portal avoids clipping by the horizontally scrollable job table.
 * Keyboard focus exposes the same details as mouse hover.
 */
export function Stage1JobStatusBadge({ row }: { row: KeywordImageRow }) {
  const partial = row.status === "completed" && row.error_code === "stage1_partial_output";
  const label = partial ? "Partial" : STATUS_LABEL[row.status];
  const detail = partial || row.status === "failed"
    ? row.error_message?.trim() || row.error_code?.trim() || (partial ? "Only some images were saved." : "Generation failed.")
    : null;
  const tooltipId = useId();
  const badgeRef = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [placement, setPlacement] = useState<{ left: number; top: number; above: boolean } | null>(null);

  const updatePlacement = useCallback(() => {
    const rect = badgeRef.current?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(320, Math.max(0, window.innerWidth - 24));
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    const above = window.innerHeight - rect.bottom < 135 && rect.top > 135;
    setPlacement({ left, top: above ? rect.top - 7 : rect.bottom + 7, above });
  }, []);

  useEffect(() => {
    if (!visible) return;
    // The scrollable table and page can move independently of each other.
    // Keep the tooltip anchored without changing row height or layout.
    window.addEventListener("scroll", updatePlacement, true);
    window.addEventListener("resize", updatePlacement);
    return () => {
      window.removeEventListener("scroll", updatePlacement, true);
      window.removeEventListener("resize", updatePlacement);
    };
  }, [visible, updatePlacement]);

  const showDetail = () => {
    if (!detail) return;
    updatePlacement();
    setVisible(true);
  };

  return <>
    <span ref={badgeRef}
      className={"rrugc-keyword-gen-badge status-" + row.status + (detail ? " has-detail" : "")}
      tabIndex={detail ? 0 : undefined}
      aria-label={detail ? label + ": " + detail : undefined}
      aria-describedby={visible && detail && placement ? tooltipId : undefined}
      onMouseEnter={detail ? showDetail : undefined}
      onMouseLeave={detail ? () => setVisible(false) : undefined}
      onFocus={detail ? showDetail : undefined}
      onBlur={detail ? () => setVisible(false) : undefined}
      onKeyDown={detail ? event => { if (event.key === "Escape") setVisible(false); } : undefined}>
      <i />{label}
    </span>
    {visible && detail && placement && createPortal(
      <div id={tooltipId} role="tooltip" className="rrugc-keyword-gen-error-tooltip"
        style={{
          left: placement.left, top: placement.top,
          transform: placement.above ? "translateY(-100%)" : undefined,
        }}>{detail}</div>, document.body,
    )}
  </>;
}

const EMPTY: KeywordImagePage = {
  items: [], total: 0, page: 1, page_size: 20,
  overview: { not_run: 0, queued: 0, running: 0, completed: 0, failed: 0 },
};

type KeywordActionIcon = "generate" | "regenerate" | "retry" | "versions" | "logs";

function KeywordActionGlyph({ kind }: { kind: KeywordActionIcon }) {
  if (kind === "logs") return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 3h10l3 3v15H4V3h3Z"/><path d="M8 10h8M8 14h8M8 18h5"/></svg>;
  if (kind === "versions") return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="7" y="7" width="13" height="13" rx="2"/><path d="M4 16V5a2 2 0 0 1 2-2h11"/></svg>;
  if (kind === "generate") return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="m12 3 1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6L12 3ZM19 17v4m-2-2h4"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 0-2.5 6"/><path d="M20 4v7h-7"/></svg>;
}

export function KeywordImageRowControls({
  row, busy, canGenerate, onRun, onPublishAvailable, onLogs, showPreview = true,
}: {
  row: KeywordImageRow;
  busy: boolean;
  canGenerate: boolean;
  onRun: (action: Stage1ImageAction) => void;
  onPublishAvailable?: () => void;
  onLogs: () => void;
  showPreview?: boolean;
}) {
  const [confirmAction, setConfirmAction] = useState<"retry" | "regenerate" | null>(null);
  const triggerButtonRef = useRef<HTMLButtonElement | null>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const acceptButtonRef = useRef<HTMLButtonElement>(null);

  const initial = row.status === "not_run";
  const terminal = row.status === "completed" || row.status === "failed";
  const canRetryFailedV3 = row.status === "failed"
    && row.skill_name === "creative-brief-v3" && Boolean(row.processing_job_id);
  const actionDisabled = busy || !canGenerate;

  useEffect(() => {
    if (!confirmAction) return;
    cancelButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setConfirmAction(null);
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
      triggerButtonRef.current?.focus();
    };
  }, [confirmAction]);

  const askConfirm = (action: "retry" | "regenerate", button: HTMLButtonElement) => {
    triggerButtonRef.current = button;
    setConfirmAction(action);
  };

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
      {initial && <button type="button" className="rrugc-keyword-result-action is-generate"
        onClick={() => onRun("generate")} disabled={actionDisabled}
        title={actionDisabled ? "Creative Brief V3 is unavailable" : "Generate six designs from Creative Brief V3"}
        aria-label={"Generate " + row.keyword}>
        <KeywordActionGlyph kind="generate" /><span className="rrugc-keyword-action-label">Generate</span>
      </button>}
      {canRetryFailedV3 && <button type="button" className="rrugc-keyword-result-action is-retry"
        onClick={event => askConfirm("retry", event.currentTarget)} disabled={actionDisabled}
        title="Retry the same failed V3 attempt; reuse verified checkpoints and uploaded assets"
        aria-label={"Retry " + row.keyword}>
        <KeywordActionGlyph kind="retry" /><span className="rrugc-keyword-action-label">Retry</span>
      </button>}
      {terminal && <button type="button" className="rrugc-keyword-result-action is-regenerate"
        onClick={event => askConfirm("regenerate", event.currentTarget)} disabled={actionDisabled}
        title="Regenerate: create six fresh V3 designs; keep all previous Output History"
        aria-label={"Regenerate " + row.keyword}>
        <KeywordActionGlyph kind="regenerate" /><span className="rrugc-keyword-action-label">Regenerate</span>
      </button>}
      {row.partial_publish_available && onPublishAvailable && <button type="button" className="rrugc-keyword-result-action is-generate"
        onClick={onPublishAvailable} disabled={busy}
        title="Publish only previously edited genuine images; no new AI calls or replacement"
        aria-label={"Publish " + row.checkpoint_count + " existing images for " + row.keyword}>
        <KeywordActionGlyph kind="generate" />
        <span className="rrugc-keyword-action-label">Publish {row.checkpoint_count} images</span>
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
      {row.retry_count > 0 && row.status !== "completed" && <span className="rrugc-keyword-auto-recovery" title={row.next_retry_at ? "Next retry " + new Date(row.next_retry_at).toLocaleString() : "Automatic recovery budget"}>
        {row.status === "failed" ? "Needs attention" : "Auto recovery"} {Math.min(row.retry_count, row.auto_retry_max ?? 3)}/{row.auto_retry_max ?? 3}
        {(row.checkpoint_count ?? 0) > 0 ? " · " + row.checkpoint_count + "/" + (row.expected_output_count ?? 6) + " checkpoint" : ""}
      </span>}
    </div>
    {confirmAction && createPortal(
      <div className="rrugc-keyword-regenerate-backdrop" onMouseDown={event => {
        if (event.target === event.currentTarget) setConfirmAction(null);
      }}>
        <section className="rrugc-keyword-regenerate-dialog" role="dialog" aria-modal="true"
          aria-labelledby="rrugc-regenerate-title" aria-describedby="rrugc-regenerate-description">
          <div className="rrugc-keyword-regenerate-icon"><KeywordActionGlyph kind={confirmAction} /></div>
          <h2 id="rrugc-regenerate-title">{confirmAction === "retry" ? "Retry failed V3 attempt?" : "Regenerate six new designs?"}</h2>
          <p id="rrugc-regenerate-description">
            <strong>{row.keyword}</strong> — {confirmAction === "retry"
              ? "Continue the same failed Creative Brief V3 attempt. Valid PNG checkpoints and already uploaded images will be reused, so completed slots will not be generated again."
              : "Start a brand-new Creative Brief V3 batch (3 concepts × 2 variants). No images from the previous attempt will be reused."}
            {" "}All existing Stage 1 images and Output History stay protected.
          </p>
          <div className="rrugc-keyword-regenerate-buttons">
            <button ref={cancelButtonRef} type="button" onClick={() => setConfirmAction(null)}>Cancel</button>
            <button ref={acceptButtonRef} type="button" className="is-accept" onClick={() => {
              const action = confirmAction;
              setConfirmAction(null);
              onRun(action);
            }}>{confirmAction === "retry" ? "Retry failed run" : "Regenerate"}</button>
          </div>
        </section>
      </div>, document.body,
    )}
  </div>;
}

export function KeywordImageStage({
  active,
}: {
  active: boolean;
  skillCatalogRevision: number;
  onManageSkills: () => void;
}) {
  const [data, setData] = useState<KeywordImagePage>(EMPTY);
  // The deployed Stage 0 detail API already exposes picked_at. Read it only for
  // legacy Stage 1 responses until the new Stage 1 backend is safely released.
  const [legacyPickedDates, setLegacyPickedDates] = useState<Record<string, string | null>>({});
  const pickedDateRequests = useRef(new Set<string>());
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [status, setStatus] = useState<"all" | KeywordImageStatus>("all");
  const [sortBy, setSortBy] = useState<KeywordImageSortBy>("finished_at");
  const pickedSortSupported = data.items.length > 0 && data.items.every(item => item.picked_at !== undefined);
  const [sortDirection, setSortDirection] = useState<KeywordImageSortDirection>("desc");
  const onSort = (column: KeywordImageSortBy) => {
    setSortDirection(current => column === sortBy ? (current === "asc" ? "desc" : "asc") : column === "keyword" ? "asc" : "desc");
    setSortBy(column);
    setPage(1);
  };
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [workingIds, setWorkingIds] = useState<Set<string>>(new Set());
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [versionsJob, setVersionsJob] = useState<{ jobId: string; title: string; initialVersion?: number } | null>(null);
  const [pickedModalOpen, setPickedModalOpen] = useState(false);
  const [briefTarget, setBriefTarget] = useState<{ id: string; keyword: string; hasJob: boolean; jobStatus: KeywordImageStatus } | null>(null);
  const [briefBatchRunning, setBriefBatchRunning] = useState(false);
  const [creativeV3Ready, setCreativeV3Ready] = useState(false);
  const [pickedDesignOverrides, setPickedDesignOverrides] = useState<Record<string, Record<number, boolean>>>({});
  const [keywordDetailTarget, setKeywordDetailTarget] = useState<{ id: string; keyword: string } | null>(null);
  const [keywordDetail, setKeywordDetail] = useState<KeywordVolume | null>(null);
  const [keywordDetailLoading, setKeywordDetailLoading] = useState(false);
  const [keywordDetailError, setKeywordDetailError] = useState("");
  const [stageView, setStageView] = useState<TypographyStageView>(() => {
    const view = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("view") : "";
    return view === "ideas" || view === "style-lab" ? view : "jobs";
  });
  const [selectedStyleKeyword, setSelectedStyleKeyword] = useState<StyleLabKeyword | null>(null);
  const [ideasAccess, setIdeasAccess] = useState<TypographyReferencePage | null>(null);
  const [v2Access, setV2Access] = useState<"checking" | "available" | "unavailable">("checking");
  const [ideasCount, setIdeasCount] = useState(0);
  const onIdeasCount = useCallback((count: number) => setIdeasCount(count), []);
  // Navigation is always visible. The server still controls private V2 access.
  const showKeywordJobs = stageView === "jobs";
  const jobsActive = active && showKeywordJobs;
  const changeStageView = (next: TypographyStageView) => {
    setStageView(next);
    const url = new URL(window.location.href);
    if (next === "jobs") url.searchParams.delete("view");
    else url.searchParams.set("view", next);
    window.history.pushState({}, "", url.pathname + url.search + url.hash);
    setPickedModalOpen(false);
    setKeywordDetailTarget(null);
  };
  useEffect(() => {
    const onPopState = () => {
      const view = new URLSearchParams(window.location.search).get("view");
      setStageView(view === "ideas" || view === "style-lab" ? view : "jobs");
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  useEffect(() => {
    if (!active) return;
    // Authenticated /typography/status is a 200 capability probe, even when
    // the canary is OFF. Never hide the navigation based on a build-time env.
    const ctrl = new AbortController();
    setV2Access("checking");
    void getTypographyStatus(ctrl.signal)
      .then(status => {
        if (ctrl.signal.aborted) return;
        if (!status.available) {
          setIdeasAccess(null); setIdeasCount(0); setV2Access("unavailable");
          return;
        }
        return listTypographyReferences({ limit: 1 }, ctrl.signal).then(page => {
          if (!ctrl.signal.aborted) {
            setIdeasAccess(page); setIdeasCount(page.stats.total); setV2Access("available");
          }
        });
      })
      .catch(() => {
        if (!ctrl.signal.aborted) {
          setIdeasAccess(null); setIdeasCount(0); setV2Access("unavailable");
        }
      });
    return () => ctrl.abort();
  }, [active]);

  useEffect(() => {
    const timer = window.setTimeout(() => { setDebouncedQuery(query.trim()); setPage(1); }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!jobsActive) return;
    const controller = new AbortController();
    void getCreativeV3GenerationStatus(controller.signal)
      .then(result => { if (!controller.signal.aborted) setCreativeV3Ready(result.available && result.output_count === 6); })
      .catch(() => { if (!controller.signal.aborted) setCreativeV3Ready(false); });
    return () => controller.abort();
  }, [jobsActive]);

  const refresh = (signal?: AbortSignal) => listKeywordImages(
    { page, pageSize, query: debouncedQuery, status, sortBy, sortDirection }, signal,
  ).then(value => {
    if (!signal?.aborted) {
      setData(value);
      setLoading(false);
    }
  });

  useEffect(() => {
    if (!jobsActive) return;
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
  }, [jobsActive, page, pageSize, debouncedQuery, status, sortBy, sortDirection]);

  // Bounded, cached compatibility reads; never refetch every 5s poll.
  // The API's tenant-scoped detail route is already available in production.
  const legacyIds = data.items.filter(row => row.picked_at === undefined)
    .map(row => row.keyword_id).join("|");
  useEffect(() => {
    if (!jobsActive || !legacyIds) return;
    const pending = legacyIds.split("|").filter(id => !pickedDateRequests.current.has(id));
    if (!pending.length) return;
    const controller = new AbortController();
    let cursor = 0;
    const worker = async () => {
      while (!controller.signal.aborted && cursor < pending.length) {
        const id = pending[cursor++];
        pickedDateRequests.current.add(id);
        try {
          const detail = await getKeywordAnalysisDetail(id, controller.signal);
          if (!controller.signal.aborted) {
            setLegacyPickedDates(current => ({ ...current, [id]: detail.picked_at ?? null }));
          }
        } catch {
          if (!controller.signal.aborted) {
            // Missing or inaccessible data is never substituted with job dates.
            setLegacyPickedDates(current => ({ ...current, [id]: null }));
          }
        } finally {
          if (controller.signal.aborted) pickedDateRequests.current.delete(id);
        }
      }
    };
    void Promise.all(Array.from({ length: Math.min(3, pending.length) }, () => worker()));
    return () => controller.abort();
  }, [jobsActive, legacyIds]);

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

  const publishAvailable = async (row: KeywordImageRow) => {
    if (workingIds.has(row.keyword_id) || !row.partial_publish_available) return;
    setWorkingIds(current => new Set(current).add(row.keyword_id));
    setError("");
    setMessage("");
    try {
      await publishAvailableKeywordImages(row.keyword_id);
      setMessage("Publishing " + row.checkpoint_count + " existing edited images for " + row.keyword + ". No additional image generation.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not publish saved image edits.");
    } finally {
      setWorkingIds(current => { const next = new Set(current); next.delete(row.keyword_id); return next; });
    }
  };

  const runOne = async (row: KeywordImageRow, action: Stage1ImageAction) => {
    if (workingIds.has(row.keyword_id)) return;
    setWorkingIds(current => new Set(current).add(row.keyword_id));
    setError("");
    setMessage("");
    try {
      // Never fall back to the retired Google Fonts / keyword-only Skills.
      // The backend enforces this as well, including for stale clients and
      // requests sent directly to the former Stage 1 endpoints.
      const capability = await getCreativeV3GenerationStatus();
      if (!capability.available || capability.output_count !== 6) {
        throw new Error("Creative Brief V3 ImageGen is unavailable. No legacy Skill was run.");
      }
      setCreativeV3Ready(true);
      const outcome = await queueStage1CreativeBriefRun(
        row.keyword_id, row.keyword, action, row.processing_job_id,
      );
      if (outcome === "images_queued") {
        setMessage((action === "retry"
          ? "V3 Retry queued: continuing the failed attempt and reusing verified checkpoints for "
          : action === "regenerate" ? "Fresh six-design V3 regeneration queued for "
            : "Creative Brief V3 six-design generation queued for ") + row.keyword
          + ". Previous Output History remains protected.");
      } else {
        setBriefTarget({ id: row.keyword_id, keyword: row.keyword, hasJob: Boolean(row.job_id), jobStatus: row.status });
        setMessage(outcome === "brief_queued"
          ? "Creative Brief analysis queued for " + row.keyword + ". Open the brief to generate six images once ready."
          : "Creative Brief analysis is in progress for " + row.keyword + ". Generate six images when analysis completes.");
      }
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to queue Creative Brief V3.");
    } finally {
      setWorkingIds(current => { const next = new Set(current); next.delete(row.keyword_id); return next; });
    }
  };

  const totalUsed = Object.values(data.overview).reduce((sum, count) => sum + count, 0);
  const first = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const last = Math.min(data.total, data.page * data.page_size);
  const pageCount = Math.max(1, Math.ceil(data.total / data.page_size));
  return <div className="rrugc-keyword-gen">
    <RrugcStageHeader
      kicker={showKeywordJobs ? "STAGE 1 / KEYWORD-TO-IMAGE" : stageView === "ideas" ? "STAGE 1 / TYPOGRAPHY IDEAS" : "STAGE 1 / STYLE LAB"}
      title={showKeywordJobs ? "Generate images from used keywords" : stageView === "ideas" ? "Typography Ideas Library" : "Balanced Typography Style Lab"}
      description={showKeywordJobs ? "Generate six original artwork designs per keyword from its Creative Brief: three directions, two variants each. Legacy Google Fonts generation is retired."
        : stageView === "ideas" ? "Explore, upload and review typography references before using them in new keyword designs."
          : "Explore original vector drafts with approved Style DNA. No AI generation or job is created."}
      actions={showKeywordJobs ? <div className="rrugc-keyword-gen-actions">
        <button type="button" className="typography-v2-entry" data-testid="typography-v2-entry"
          onClick={() => changeStageView("style-lab")} title="Open Typography V2 MVP workspace">
          Typography V2 <span>MVP</span>
        </button>
        <label className="rrugc-keyword-gen-search">
          <span className="sr-only">Search used keywords</span>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/></svg>
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search used keywords…" />
        </label>
        <button type="button" className="rrugc-keyword-gen-primary rrugc-icon-action"
          disabled={briefBatchRunning} onClick={() => {
            setBriefBatchRunning(true);
            void backfillKeywordCreativeBriefs().then(result => {
              setMessage("Creative Brief: " + result.queued + " jobs queued · " + result.skipped + " already analyzed or queued.");
              void refresh();
            }).catch(reason => setError(reason instanceof Error ? reason.message : "Unable to queue briefs."))
              .finally(() => setBriefBatchRunning(false));
          }}><RrugcActionIcon name="sparkles" />{briefBatchRunning ? "Queueing briefs…" : "Analyze briefs"}</button>
        <button type="button" className="rrugc-keyword-gen-primary rrugc-icon-action" onClick={() => setPickedModalOpen(true)}><RrugcActionIcon name="check" />Picked design</button>
      </div> : undefined}
    />
    <Stage1Subnav view={stageView} onView={changeStageView} ideasCount={ideasCount} />
    {stageView !== "jobs" && !ideasAccess && <section className="typography-v2-availability"
      data-testid="typography-v2-availability" role="status">
      <span className="typography-v2-availability-kicker">TYPOGRAPHY V2 · MVP</span>
      <h3>{v2Access === "checking" ? "Checking Typography V2 access…" : "Typography V2 is not enabled for this workspace"}</h3>
      <p>{v2Access === "checking"
        ? "Checking your workspace and read permission without starting image generation."
        : "The new interface is deployed. Access to private Typography references and Style Lab is controlled by the tenant canary and the typography_reference.read permission. No ImageGen jobs or image uploads were started."}</p>
      <button type="button" onClick={() => changeStageView("jobs")}>Back to Keyword Jobs</button>
    </section>}
    {ideasAccess && <div hidden={stageView !== "ideas"} className="typography-ideas-view">
      <TypographyIdeasPage active={active && stageView === "ideas"} firstPage={ideasAccess} onCountChange={onIdeasCount} />
    </div>}
    {ideasAccess && stageView === "style-lab" && <TypographyStyleLab
      active={active}
      keywords={data.items.map(item => ({ keyword_id: item.keyword_id, keyword: item.keyword }))}
      selectedKeyword={selectedStyleKeyword}
      onBack={() => changeStageView("jobs")} />}
    <div hidden={!showKeywordJobs} className="typography-keyword-jobs-shell">
    <div className="rrugc-keyword-gen-stats" aria-label="Stage 1 generation status">
      <button type="button" aria-pressed={status === "all"} className={status === "all" ? "active" : ""} onClick={() => { setStatus("all"); setPage(1); }}>
        <strong>{totalUsed.toLocaleString()}</strong><span>All used</span>
      </button>
      {STATUS_ORDER.map(key => <button key={key} type="button" aria-pressed={status === key} className={status === key ? "active" : ""} onClick={() => { setStatus(key); setPage(1); }}>
        <strong>{(data.overview[key] || 0).toLocaleString()}</strong><span>{STATUS_LABEL[key]}</span>
      </button>)}
    </div>

    {pickedModalOpen && <PickedDesignModal onClose={() => setPickedModalOpen(false)} onUnpick={(jobId, version) => setPickedDesignOverrides(current => ({ ...current, [jobId]: { ...current[jobId], [version]: false } }))} />}
    <ActionMessageToast message={error} tone="error" />
    <ActionMessageToast message={message} />
    {!creativeV3Ready && <p className="rrugc-keyword-gen-hint" role="status">Creative Brief V3 is the only Stage 1 generation engine. ImageGen is unavailable for this workspace until enabled.</p>}
    <div className="rrugc-keyword-gen-list-heading">
      <div className="rrugc-keyword-gen-list-title">
        <strong>Keyword jobs</strong>
        <span>Meaning-first artwork · 3 concepts × 2 variants · transparent PNG · historical outputs are preserved.</span>
      </div>
      <span className="rrugc-keyword-gen-list-count">{first}–{last} of {data.total.toLocaleString()} jobs</span>
    </div>
    <div className="rrugc-keyword-gen-table-wrap" aria-busy={loading} role="region" aria-label="Stage 1 keyword jobs scrollable table" tabIndex={0}>
      <table className="rrugc-keyword-gen-table" data-sort-column={sortBy} data-sort-direction={sortDirection}>
        <thead><tr>
          <Stage1SortHeader column="keyword" label="Keyword / Source" sortBy={sortBy} sortDirection={sortDirection} onSort={onSort} />
          <th>Trend</th>
          <Stage1SortHeader column="search_volume" label="Volume" sortBy={sortBy} sortDirection={sortDirection} onSort={onSort} />
          <th>Creative Brief</th>
          <th>Generation engine</th><th>Job status</th>
          {pickedSortSupported
            ? <Stage1SortHeader column="picked_at" label="Picked date" sortBy={sortBy} sortDirection={sortDirection} onSort={onSort} />
            : <th title="Sorting by picked date will be available after the Stage 1 API upgrade">Picked date</th>}
          <Stage1SortHeader column="duration" label="Duration" sortBy={sortBy} sortDirection={sortDirection} onSort={onSort} />
          <Stage1SortHeader column="finished_at" label="Finished at" sortBy={sortBy} sortDirection={sortDirection} onSort={onSort} />
          <th>Output</th><th>Actions</th>
        </tr></thead>
        <tbody>
          {loading && !data.items.length
            ? Array.from({ length: 6 }, (_, index) => <tr key={index} className="rrugc-keyword-gen-loading"><td colSpan={11}><span /></td></tr>)
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
                <td className="rrugc-keyword-gen-trend"><KeywordTrendChart item={row} onOpen={() => setKeywordDetailTarget({ id: row.keyword_id, keyword: row.keyword })} /></td>
                <td className="rrugc-keyword-gen-volume">{row.search_volume.toLocaleString("en-US")}</td>
                <td className="rrugc-creative-brief-cell">
                  <button type="button" onClick={() => setBriefTarget({ id: row.keyword_id, keyword: row.keyword, hasJob: Boolean(row.job_id), jobStatus: row.status })}
                    title={"View Creative Brief for " + row.keyword}
                    aria-label={"View Creative Brief for " + row.keyword}>
                    <strong>{row.creative_brief?.status === "completed"
                      ? (row.creative_brief.strategy || "Meaning-First").replaceAll("_", "-")
                      : row.creative_brief?.status === "queued" ? "Queued"
                        : row.creative_brief?.status === "running" ? "Analyzing…"
                          : row.creative_brief?.status === "failed" ? "Retry analysis" : "Not analyzed"}</strong>
                    <small>{row.creative_brief?.status === "completed"
                      ? row.creative_brief.meaning_emotion || row.creative_brief.creative_hook
                      : row.creative_brief?.status === "queued" ? "Waiting for Codex"
                        : row.creative_brief?.status === "running" ? "Text-only Codex"
                          : "View concepts"}</small>
                    {row.creative_brief?.status === "completed" && <em>{row.creative_brief.concept_count} concepts · View</em>}
                  </button>
                </td>
                <td><div className="rrugc-keyword-gen-skill-cell">
                  <span className="rrugc-keyword-gen-skill-name" title={row.skill_name && row.skill_name !== "creative-brief-v3"
                    ? "Historical output · retired engine: " + row.skill_name
                    : "Meaning-first Creative Brief V3"}>{row.job_id && row.skill_name !== "creative-brief-v3"
                      ? "Legacy output" : "Creative Brief V3"}</span>
                  <small>{row.job_id && row.skill_name !== "creative-brief-v3"
                    ? "Archived · Regenerate with V3" : row.status === "not_run" ? "Next run" : "3 concepts × 2 variants"}</small>
                </div></td>
                <td><div className="rrugc-keyword-gen-status-cell">
                  <Stage1JobStatusBadge row={row} />
                  {row.retry_count > 0 && <small className="rrugc-keyword-auto-recovery" title={row.next_retry_at ? "Next retry: " + new Date(row.next_retry_at).toLocaleString() : "Durable Stage 1 recovery"}>
                    {row.status === "failed" ? "Needs attention" : "Auto recovery"} {Math.min(row.retry_count, row.auto_retry_max ?? 3)}/{row.auto_retry_max ?? 3}
                    {(row.checkpoint_count ?? 0) > 0 ? " · " + row.checkpoint_count + "/" + (row.expected_output_count ?? 6) + " checkpoint" : ""}
                    {row.next_retry_at && row.status === "queued" ? " · Next " + new Date(row.next_retry_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                  </small>}
                  {row.status === "running" || row.status === "queued" ? <small className="rrugc-keyword-gen-attempt">{row.status === "queued" ? "Waiting for worker" : "Running image-generation Skill"}</small> : null}
                  {row.status === "not_run" && <small className="rrugc-keyword-gen-attempt">Ready to generate</small>}
                </div></td>
                <td className="rrugc-keyword-gen-picked">{(() => {
                  const pickedAt = row.picked_at === undefined ? legacyPickedDates[row.keyword_id] : row.picked_at;
                  return pickedAt && formatStage0PickedAt(pickedAt) !== "—"
                    ? <time dateTime={pickedAt} title={formatKeywordJobFinishedAtAbsolute(pickedAt)}>{formatStage0PickedAt(pickedAt)}</time>
                    : <span className="rrugc-keyword-gen-muted">—</span>;
                })()}</td>
                <td className="rrugc-keyword-gen-duration" title={row.started_at || "Job has not started"}>{row.started_at
                  ? formatKeywordJobDuration(row.started_at, row.finished_at) : "—"}{row.started_at && !row.finished_at && row.status === "running" && <small>Running</small>}</td>
                <td className="rrugc-keyword-gen-finished">{row.finished_at
                  ? <time dateTime={row.finished_at} title={formatKeywordJobFinishedAtAbsolute(row.finished_at)}>{formatKeywordJobFinishedAt(row.finished_at)}</time>
                  : <span className="rrugc-keyword-gen-muted">{row.status === "running" ? "In progress" : row.status === "queued" ? "In queue" : "—"}</span>}</td>
                <td className="rrugc-keyword-gen-output-column"><KeywordImageOutputSlider row={row}
                  pickedOverrides={row.job_id ? pickedDesignOverrides[row.job_id] : undefined}
                  onOpenVersion={version => { if (row.job_id) setVersionsJob({ jobId: row.job_id, title: row.keyword, initialVersion: version }); }} /></td>
                <td className="rrugc-keyword-gen-result">
                  <KeywordImageRowControls row={row} busy={busy} canGenerate={creativeV3Ready} showPreview={false}
                    onRun={action => void runOne(row, action)}
                    onPublishAvailable={() => void publishAvailable(row)}
                    onLogs={() => { if (row.job_id) setLogJobId(row.job_id); }} />
                  {ideasAccess && <button type="button" className="typography-style-lab-open"
                    aria-label={"Apply style to " + row.keyword}
                    title="Open preview-only Style Lab (no image generation)"
                    onClick={() => { setSelectedStyleKeyword({ keyword_id: row.keyword_id, keyword: row.keyword }); changeStageView("style-lab"); }}>
                    Apply style
                  </button>}
                </td>
              </tr>;
            })}
          {!loading && data.items.length === 0 && <tr><td colSpan={11}><div className="rrugc-keyword-gen-empty"><strong>No keywords in this view</strong><p>Mark keywords as Used in Stage 0, or clear the current search/status filter.</p></div></td></tr>}
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
    {briefTarget && <CreativeBriefDialog key={briefTarget.id} keywordId={briefTarget.id} keyword={briefTarget.keyword}
      hasExistingStage1Job={briefTarget.hasJob} stage1Status={briefTarget.jobStatus}
      onClose={() => setBriefTarget(null)} />}
    {logJobId && <SkillJobLogDialog stage="stage1" jobId={logJobId} onClose={() => setLogJobId(null)} />}
    {versionsJob && <GenerationOutputVersionsDialog stage="stage1" jobId={versionsJob.jobId} title={versionsJob.title}
      initialVersion={versionsJob.initialVersion} onClose={() => setVersionsJob(null)}
      onProductSelectionChange={(version, selected) => setPickedDesignOverrides(current => ({
        ...current,
        [versionsJob.jobId]: { ...(current[versionsJob.jobId] ?? {}), [version]: selected },
      }))} />}
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
    </div>
  </div>;
}