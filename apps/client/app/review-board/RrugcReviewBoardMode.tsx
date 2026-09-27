import { useCallback, useEffect, useMemo, useState } from "react";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { WorkspacePageHeader } from "../components/WorkspacePageHeader";
import {
  approveReviewTask,
  listReviewTasks,
  rejectReviewTask,
} from "../realistic-review-ugc/api";
import type {
  ReviewTask,
  ReviewTaskList,
} from "../realistic-review-ugc/types";

type RrugcReviewStatus = "all" | ReviewTask["status"];
type RrugcReviewPriority = "" | ReviewTask["priority"];

const PAGE_SIZE = 25;

const formatTime = (value: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? date.toLocaleString() : "—";
};

const metricPercent = (value: number | null | undefined) =>
  typeof value === "number" ? Math.round(value * 100) + "%" : "—";

export function ReviewBoardModeSwitch({
  mode,
  canShared,
  canRrugc,
  onMode,
}: {
  mode: "shared" | "rrugc";
  canShared: boolean;
  canRrugc: boolean;
  onMode: (mode: "shared" | "rrugc") => void;
}) {
  return (
    <div className="review-board-mode-switch" role="tablist" aria-label="Review source">
      {canShared && (
        <button
          type="button"
          role="tab"
          aria-selected={mode === "shared"}
          className={mode === "shared" ? "active" : undefined}
          onClick={() => onMode("shared")}
        >
          Shared feedback
        </button>
      )}
      {canRrugc && (
        <button
          type="button"
          role="tab"
          aria-selected={mode === "rrugc"}
          className={mode === "rrugc" ? "active" : undefined}
          onClick={() => onMode("rrugc")}
        >
          Realistic UGC
        </button>
      )}
    </div>
  );
}

export function RrugcReviewTaskRow({
  task,
  selected,
  onSelect,
}: {
  task: ReviewTask;
  selected: boolean;
  onSelect: () => void;
}) {
  const stateLabel =
    task.export_status === "exported"
      ? "Cataloged"
      : task.status === "approved"
        ? "Export ready"
        : task.status === "rejected"
          ? "Not exportable"
          : "Pending review";
  return (
    <button
      type="button"
      className={
        "rrugc-review-row"
        + (selected ? " selected" : "")
        + (task.priority === "high" ? " high-priority" : "")
      }
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className={"rrugc-review-priority " + task.priority}>
        {task.priority === "high" ? "High priority" : "Standard"}
      </span>
      <span className="rrugc-review-row-copy">
        <span className="rrugc-review-row-title">
          <strong>{task.product_sku || task.product_name || "Product"}</strong>
          <em className={"rrugc-review-state " + task.status}>{stateLabel}</em>
        </span>
        <span>{task.product_name}</span>
        <small>
          {task.campaign_name} · variant {task.generation_variant}
        </small>
        <small>
          {task.supervisor_status === "needs_human_review"
            ? "Supervisor requires human review"
            : "Supervisor passed"}
          {task.supervisor_reason ? " · " + task.supervisor_reason : ""}
        </small>
      </span>
    </button>
  );
}

export function RrugcReviewInspector({
  task,
  canRun,
  note,
  busy,
  error,
  onNote,
  onApprove,
  onReject,
}: {
  task: ReviewTask | null;
  canRun: boolean;
  note: string;
  busy: boolean;
  error: string;
  onNote: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  if (!task) {
    return (
      <aside className="review-board-detail rrugc-review-detail">
        <div className="review-board-detail-empty">
          <strong>Generation review</strong>
          <p>Select a Realistic UGC task to inspect Supervisor evidence.</p>
        </div>
      </aside>
    );
  }

  const metrics = task.supervisor_metrics || {};
  return (
    <aside className="review-board-detail rrugc-review-detail" aria-label="Realistic UGC review details">
      <header className="review-board-detail-header">
        <div>
          <small>REALISTIC UGC REVIEW</small>
          <h2>{task.product_sku || task.product_name}</h2>
        </div>
        <div className="review-board-detail-badges">
          <span className={"rrugc-review-priority " + task.priority}>
            {task.priority === "high" ? "High priority" : "Standard"}
          </span>
          <span className={"rrugc-review-state " + task.status}>
            {task.export_status === "exported"
              ? "Cataloged"
              : task.status === "approved"
                ? "Export ready"
                : task.status === "rejected"
                  ? "Not exportable"
                  : "Pending"}
          </span>
        </div>
      </header>

      <section className="rrugc-review-supervisor">
        <small>SUPERVISOR</small>
        <strong>
          {task.supervisor_status === "needs_human_review"
            ? "Needs human review"
            : "Passed"}
        </strong>
        {task.supervisor_reason && <b>{task.supervisor_reason}</b>}
        {task.supervisor_summary && <p>{task.supervisor_summary}</p>}
        <div className="rrugc-review-metrics">
          <span>Product <b>{metricPercent(metrics.product_visual_similarity)}</b></span>
          <span>Placement <b>{metricPercent(metrics.placement_score)}</b></span>
          <span>Scene <b>{metricPercent(metrics.person_scene_preservation)}</b></span>
          <span>Artifact risk <b>{metricPercent(metrics.artifact_risk)}</b></span>
        </div>
      </section>

      <dl className="review-board-metadata">
        <div><dt>Campaign</dt><dd>{task.campaign_name}</dd></div>
        <div><dt>Variant</dt><dd>{task.generation_variant}</dd></div>
        <div><dt>Queue reason</dt><dd>{task.queue_reason}</dd></div>
        <div><dt>Created</dt><dd>{formatTime(task.created_at)}</dd></div>
        {task.reviewed_at && (
          <div className="wide">
            <dt>Reviewed</dt>
            <dd>{formatTime(task.reviewed_at)} · {task.reviewed_by_user_id || "Reviewer"}</dd>
          </div>
        )}
      </dl>

      {task.status === "pending" && canRun && (
        <section className="rrugc-review-decision">
          <label>
            <span>Review note</span>
            <textarea
              value={note}
              maxLength={1000}
              placeholder="Optional note for approval or rejection"
              onChange={(event) => onNote(event.target.value)}
            />
          </label>
          <div>
            <button type="button" className="rrugc-review-reject" disabled={busy} onClick={onReject}>
              {busy ? "Saving…" : "Reject"}
            </button>
            <button type="button" className="rrugc-review-approve" disabled={busy} onClick={onApprove}>
              {busy ? "Saving…" : "Approve"}
            </button>
          </div>
        </section>
      )}
      {task.review_note && (
        <section className="rrugc-review-final-note">
          <small>REVIEW NOTE</small>
          <p>{task.review_note}</p>
        </section>
      )}
      {error && <p className="review-board-error" role="alert">{error}</p>}
    </aside>
  );
}

export function RrugcReviewBoardMode({
  canShared,
  canRrugc,
  canRun,
  onMode,
}: {
  canShared: boolean;
  canRrugc: boolean;
  canRun: boolean;
  onMode: (mode: "shared" | "rrugc") => void;
}) {
  const [status, setStatus] = useState<RrugcReviewStatus>("pending");
  const [priority, setPriority] = useState<RrugcReviewPriority>("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ReviewTaskList | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    if (!canRrugc) return;
    setLoading(true);
    setError("");
    try {
      const next = await listReviewTasks({
        status: status === "all" ? undefined : status,
        priority: priority || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setPage(next);
      setSelectedId((current) => {
        if (current && next.items.some((item) => item.id === current)) return current;
        return next.items[0]?.id || null;
      });
    } catch {
      setError("Realistic UGC review tasks are unavailable. Refresh and try again.");
    } finally {
      setLoading(false);
    }
  }, [canRrugc, offset, priority, status]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const selected = useMemo(
    () => page?.items.find((item) => item.id === selectedId) || null,
    [page, selectedId],
  );

  useEffect(() => {
    setNote(selected?.review_note || "");
  }, [selected?.id, selected?.review_note]);

  const transition = async (action: "approve" | "reject") => {
    if (!selected || !canRun || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = action === "approve"
        ? await approveReviewTask(selected.id, note)
        : await rejectReviewTask(selected.id, note);
      setPage((current) => current ? {
        ...current,
        items: current.items.map((item) =>
          item.id === result.task.id ? result.task : item),
      } : current);
      if (status === "pending") await reload();
    } catch {
      setError("The review decision was not saved. Refresh and try again.");
    } finally {
      setBusy(false);
    }
  };

  const pendingCount = page?.items.filter((item) => item.status === "pending").length ?? 0;
  const highCount = page?.items.filter((item) => item.priority === "high" && item.status === "pending").length ?? 0;

  return (
    <main className="review-board-shell">
      <aside className="ops-sidebar">
        <div className="brand">
          <b><BrandIcon /></b>
          <span><strong>Creative assets</strong><small>Review operations</small></span>
        </div>
        <WorkspaceNavigation active="review-board" showReviewBoard />
        <small className="ops-sidebar-note">Review Realistic UGC outputs before export.</small>
      </aside>

      <section className="review-board-main" aria-busy={loading}>
        <WorkspacePageHeader
          className="review-board-header"
          route="review-board"
          titleAddon={<span className="review-board-open-count">{page?.total.toLocaleString() || "0"} tasks</span>}
          meta={<span className="review-board-secondary-summary">{highCount} high priority · {pendingCount} pending on this page</span>}
          actions={<button type="button" className="review-board-refresh" onClick={() => void reload()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>}
        />

        <ReviewBoardModeSwitch
          mode="rrugc"
          canShared={canShared}
          canRrugc={canRrugc}
          onMode={onMode}
        />

        <section className="review-board-filters rrugc-review-filters" aria-label="Realistic UGC review filters">
          <div className="review-board-filter-main">
            <label>
              <span>Status</span>
              <select value={status} onChange={(event) => {
                setStatus(event.target.value as RrugcReviewStatus);
                setOffset(0);
              }}>
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="all">All</option>
              </select>
            </label>
            <label>
              <span>Priority</span>
              <select value={priority} onChange={(event) => {
                setPriority(event.target.value as RrugcReviewPriority);
                setOffset(0);
              }}>
                <option value="">All priorities</option>
                <option value="high">High priority</option>
                <option value="standard">Standard</option>
              </select>
            </label>
          </div>
        </section>

        {error && <p className="review-board-error" role="alert">{error}</p>}

        <section className="review-board-workspace rrugc-review-workspace">
          <section className="review-board-inbox" aria-label="Realistic UGC review tasks">
            <header>
              <div>
                <small>REALISTIC UGC QUEUE</small>
                <strong>{page?.total.toLocaleString() || "0"} tasks</strong>
              </div>
              {loading && <span className="review-board-inline-loading">Updating…</span>}
            </header>
            <div className="review-board-list">
              {!loading && !page?.items.length ? (
                <div className="review-board-list-empty">
                  <strong>No matching Realistic UGC tasks</strong>
                  <p>Supervisor PASS and human-review outputs will appear here.</p>
                </div>
              ) : (
                page?.items.map((task) => (
                  <RrugcReviewTaskRow
                    key={task.id}
                    task={task}
                    selected={selectedId === task.id}
                    onSelect={() => setSelectedId(task.id)}
                  />
                ))
              )}
            </div>
            {page && (
              <footer className="review-board-pagination">
                <span>
                  {page.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}
                </span>
                <div>
                  <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>‹</button>
                  <button type="button" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>›</button>
                </div>
              </footer>
            )}
          </section>

          <section className="review-board-preview rrugc-review-preview" aria-label="Generated image preview">
            <header>
              <div><small>GENERATED OUTPUT</small><h2>{selected?.product_name || "Select a task"}</h2></div>
              {selected && <span>variant {selected.generation_variant}</span>}
            </header>
            <div className="review-board-preview-stage">
              {selected ? (
                <img src={selected.output_url} alt={"Generated output for " + (selected.product_name || selected.product_sku)} />
              ) : (
                <div className="review-board-preview-empty neutral">
                  <strong>No task selected</strong>
                  <p>Select a review task to inspect its generated output.</p>
                </div>
              )}
            </div>
          </section>

          <RrugcReviewInspector
            task={selected}
            canRun={canRun}
            note={note}
            busy={busy}
            error=""
            onNote={setNote}
            onApprove={() => void transition("approve")}
            onReject={() => void transition("reject")}
          />
        </section>
      </section>
    </main>
  );
}
