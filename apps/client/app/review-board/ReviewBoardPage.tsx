import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { RichAnnotation } from "../public-review/RichAnnotation";
import {
  fetchBoardIssue,
  fetchBoardIssues,
  fetchBoardStats,
  reopenBoardIssue,
  resolveBoardIssue,
} from "./api";
import type {
  BoardFilters,
  BoardIssue,
  BoardIssueDetail,
  BoardPage,
  BoardStats,
} from "./types";

const initialFilters: BoardFilters = {
  status: "open",
  reviewer: "",
  pinned: "",
  created_from: "",
  created_to: "",
  sort: "newest",
  page: 1,
  page_size: 25,
};

const emptyStats: BoardStats = {
  total_issues: 0,
  open_issues: 0,
  resolved_issues: 0,
  resolution_rate: 0,
  assets_with_open_issues: 0,
  shares_with_open_issues: 0,
};

const time = (value: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Not available";
};

const compactTime = (value: string) => {
  const date = new Date(value);
  if (!Number.isFinite(date.valueOf())) return "";
  const elapsed = Date.now() - date.valueOf();
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (elapsed >= 0 && elapsed < hour) return `${Math.max(1, Math.floor(elapsed / minute))}m`;
  if (elapsed >= 0 && elapsed < day) return `${Math.floor(elapsed / hour)}h`;
  if (elapsed >= 0 && elapsed < 7 * day) return `${Math.floor(elapsed / day)}d`;
  return date.toLocaleDateString();
};

const inputDate = (value: string) => (value ? value.slice(0, 10) : "");

function ReviewBoardIcon({
  name,
}: {
  name: "refresh" | "filter" | "pin" | "comment" | "preview" | "resolved" | "open";
}) {
  const paths = {
    refresh: <><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></>,
    filter: <><path d="M4 6h16M7 12h10M10 18h4" /></>,
    pin: <><path d="m9 4 6 6M7 10l7-7 3 3-7 7M10 13l-5 6" /></>,
    comment: <><path d="M4 5h16v11H9l-5 4V5Z" /></>,
    preview: <><rect x="3.5" y="5" width="17" height="14" rx="2.5" /><path d="m8 14 3-3 2.5 2.5 2-2L19 15" /><circle cx="9" cy="9" r="1.2" /></>,
    resolved: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></>,
    open: <circle cx="12" cy="12" r="8" />,
  } as const;

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {paths[name]}
    </svg>
  );
}

function IssueStatus({
  status,
  compact = false,
}: {
  status: "open" | "resolved";
  compact?: boolean;
}) {
  return (
    <span className={`review-board-status ${status}${compact ? " compact" : ""}`}>
      <ReviewBoardIcon name={status === "resolved" ? "resolved" : "open"} />
      {!compact && (status === "resolved" ? "Resolved" : "Open")}
    </span>
  );
}

export function ReviewIssueRow({
  issue,
  selected,
  onSelect,
}: {
  issue: BoardIssue;
  selected: boolean;
  onSelect: () => void;
}) {
  const pinned = issue.anchor_x !== null && issue.anchor_y !== null;
  return (
    <button
      type="button"
      className={selected ? "review-board-row selected" : "review-board-row"}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <IssueStatus status={issue.status} compact />
      <span className="review-board-row-copy">
        <span className="review-board-row-title">
          <strong title={issue.asset.filename || undefined}>
            {issue.asset.filename || "Untitled asset"}
          </strong>
          <time dateTime={issue.updated_at}>{compactTime(issue.updated_at)}</time>
        </span>
        <span className="review-board-row-preview">{issue.annotation_preview || "No comment text."}</span>
        <span className="review-board-row-meta">
          <span>{issue.reviewer.display_name}</span>
          <span>{issue.share.name}</span>
          <span className="review-board-row-replies">
            <ReviewBoardIcon name="comment" />
            {issue.reply_count}
          </span>
          {pinned && (
            <span className="review-board-row-pinned">
              <ReviewBoardIcon name="pin" />
              Pinned
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

export function ReviewAssetPreview({
  detail,
  loading,
}: {
  detail: BoardIssueDetail | null;
  loading: boolean;
}) {
  const mediaType = detail?.asset.media_type || "Shared asset";
  return (
    <section className="review-board-preview" aria-label="Asset preview">
      <header>
        <div>
          <small>ASSET PREVIEW</small>
          <h2>{detail?.asset.filename || "Select an issue"}</h2>
        </div>
        {detail && <span>{mediaType}</span>}
      </header>
      <div className={`review-board-preview-stage${loading ? " loading" : ""}`}>
        {loading ? (
          <div className="review-board-preview-skeleton" aria-label="Loading asset preview" />
        ) : detail ? (
          <div className="review-board-preview-empty">
            <span className="review-board-preview-icon">
              <ReviewBoardIcon name="preview" />
            </span>
            <strong>Preview unavailable</strong>
            <p>
              This source does not expose an authenticated preview in Review Board.
              Annotation details and discussion are still available.
            </p>
            {detail.anchor_x !== null && detail.anchor_y !== null && (
              <span className="review-board-preview-pin">
                <ReviewBoardIcon name="pin" />
                Pinned annotation · {Math.round(detail.anchor_x * 100)}%, {Math.round(detail.anchor_y * 100)}%
              </span>
            )}
          </div>
        ) : (
          <div className="review-board-preview-empty neutral">
            <span className="review-board-preview-icon">
              <ReviewBoardIcon name="preview" />
            </span>
            <strong>No issue selected</strong>
            <p>Choose an issue from the inbox to inspect its asset and feedback.</p>
          </div>
        )}
      </div>
    </section>
  );
}

export function ReviewIssueInspector({
  detail,
  loading,
  canResolve,
  mutating,
  mutationError,
  onTransition,
}: {
  detail: BoardIssueDetail | null;
  loading: boolean;
  canResolve: boolean;
  mutating: boolean;
  mutationError: string;
  onTransition: () => void;
}) {
  const pinned = Boolean(detail && detail.anchor_x !== null && detail.anchor_y !== null);

  return (
    <aside className="review-board-detail" aria-live="polite" aria-label="Issue details">
      {loading ? (
        <div className="review-board-detail-loading">
          <i />
          <i />
          <i />
          <i />
        </div>
      ) : !detail ? (
        <div className="review-board-detail-empty">
          <ReviewBoardIcon name="comment" />
          <strong>Issue details</strong>
          <p>Select an issue to view its comment, reviewer, replies, and status.</p>
        </div>
      ) : (
        <>
          <header className="review-board-detail-header">
            <div>
              <small>ISSUE</small>
              <h2>{detail.asset.filename || "Untitled asset"}</h2>
            </div>
            <div className="review-board-detail-badges">
              <IssueStatus status={detail.status} />
              {pinned && (
                <span className="review-board-pin-badge">
                  <ReviewBoardIcon name="pin" />
                  Pinned
                </span>
              )}
            </div>
          </header>

          <section className="review-board-comment">
            <small>COMMENT</small>
            <div className="review-board-comment-body">
              <RichAnnotation document={detail.content_json} />
            </div>
          </section>

          <dl className="review-board-metadata">
            <div>
              <dt>Reviewer</dt>
              <dd>{detail.reviewer.display_name}</dd>
            </div>
            <div>
              <dt>Share</dt>
              <dd>{detail.share.name}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{time(detail.created_at)}</dd>
            </div>
            <div>
              <dt>Replies</dt>
              <dd>{detail.replies.length}</dd>
            </div>
            {detail.resolved_at && (
              <div className="wide">
                <dt>Resolved</dt>
                <dd>
                  {time(detail.resolved_at)} · {detail.resolver?.actor_id || "Unknown"}
                </dd>
              </div>
            )}
          </dl>

          <section className="review-board-replies">
            <header>
              <h3>Replies</h3>
              <span>{detail.replies.length}</span>
            </header>
            {detail.replies.length ? (
              <div className="review-board-reply-list">
                {detail.replies.map((reply) => (
                  <article key={reply.id} className="review-board-reply">
                    <header>
                      <strong>{reply.reviewer.display_name}</strong>
                      <time dateTime={reply.created_at}>{time(reply.created_at)}</time>
                    </header>
                    <RichAnnotation document={reply.content_json} />
                  </article>
                ))}
              </div>
            ) : (
              <p className="review-board-no-replies">No replies yet.</p>
            )}
          </section>

          {canResolve && (
            <div className="review-board-detail-actions">
              <button
                type="button"
                className={`review-board-transition ${detail.status}`}
                disabled={mutating}
                onClick={onTransition}
              >
                {mutating
                  ? "Updating…"
                  : detail.status === "open"
                    ? "Resolve issue"
                    : "Reopen issue"}
              </button>
            </div>
          )}
          {mutationError && (
            <p className="review-board-error" role="alert">
              {mutationError}
            </p>
          )}
        </>
      )}
    </aside>
  );
}

export function ReviewBoardPage() {
  const [permissions, setPermissions] = useState<string[] | null>(null);
  const [identityError, setIdentityError] = useState(false);
  const [filters, setFilters] = useState<BoardFilters>(initialFilters);
  const [page, setPage] = useState<BoardPage | null>(null);
  const [stats, setStats] = useState<BoardStats | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BoardIssueDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [mutating, setMutating] = useState(false);

  const canRead = permissions?.includes("public_review.read") === true;
  const canResolve = permissions?.includes("public_review.resolve") === true;

  const reload = useCallback(async () => {
    if (!canRead) return;
    setLoading(true);
    setError("");
    try {
      const [nextPage, nextStats] = await Promise.all([
        fetchBoardIssues(filters),
        fetchBoardStats(),
      ]);
      setPage(nextPage);
      setStats(nextStats);
      setSelectedId((current) => {
        const currentStillVisible =
          current && nextPage.items.some((item) => item.id === current);
        if (currentStillVisible) return current;
        setDetail(null);
        return nextPage.items[0]?.id || null;
      });
    } catch {
      setError("Review Board data is unavailable. Refresh and try again.");
    } finally {
      setLoading(false);
    }
  }, [canRead, filters]);

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity()
      .then((identity) => {
        if (alive) setPermissions(identity.permissions);
      })
      .catch(() => {
        if (alive) setIdentityError(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (!selectedId || !canRead) return;
    const controller = new AbortController();
    setDetailLoading(true);
    setMutationError("");
    fetchBoardIssue(selectedId, controller.signal)
      .then((value) => setDetail(value))
      .catch((reason) => {
        if (controller.signal.aborted) return;
        setDetail(null);
        setError(
          reason instanceof Error
            ? "The selected issue is unavailable."
            : "The selected issue is unavailable.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, canRead]);

  const updateFilter = <K extends keyof BoardFilters>(
    key: K,
    value: BoardFilters[K],
  ) =>
    setFilters((current) => ({
      ...current,
      [key]: value,
      page: key === "page" ? Number(value) : 1,
    }));

  const transition = async () => {
    if (!detail || !canResolve || mutating) return;
    setMutating(true);
    setMutationError("");
    try {
      const result =
        detail.status === "open"
          ? await resolveBoardIssue(detail.id)
          : await reopenBoardIssue(detail.id);
      setDetail((current) => (current ? { ...current, ...result } : current));
      await reload();
    } catch {
      setMutationError("The status was not changed. Try again.");
    } finally {
      setMutating(false);
    }
  };

  const totals = stats || emptyStats;
  const pageCount = page
    ? Math.max(1, Math.ceil(page.total / page.page_size))
    : 1;
  const resultStart =
    page && page.total ? (page.page - 1) * page.page_size + 1 : 0;
  const resultEnd = page
    ? Math.min(page.page * page.page_size, page.total)
    : 0;

  const secondarySummary = useMemo(
    () =>
      `${totals.total_issues.toLocaleString()} total issues · ${totals.shares_with_open_issues.toLocaleString()} ${totals.shares_with_open_issues === 1 ? "share" : "shares"} with open feedback`,
    [totals.total_issues, totals.shares_with_open_issues],
  );

  if (permissions === null && !identityError) {
    return (
      <main className="review-board-state" aria-busy="true">
        Loading your Review Board access…
      </main>
    );
  }

  if (identityError) {
    return (
      <main className="review-board-state" role="alert">
        Review Board is unavailable because your authenticated identity could not be loaded.
      </main>
    );
  }

  if (!canRead) {
    return (
      <main className="review-board-state" role="alert">
        You are signed in, but do not have permission to view the Review Board.
      </main>
    );
  }

  return (
    <main className="review-board-shell">
      <aside className="ops-sidebar">
        <div className="brand">
          <b>
            <BrandIcon />
          </b>
          <span>
            <strong>Creative assets</strong>
            <small>Review operations</small>
          </span>
        </div>
        <WorkspaceNavigation active="review-board" showReviewBoard={canRead} />
        <small className="ops-sidebar-note">
          Review and resolve shared asset feedback.
        </small>
      </aside>

      <section className="review-board-main" aria-busy={loading}>
        <header className="review-board-header">
          <div>
            <small>PUBLIC REVIEW</small>
            <div className="review-board-heading-row">
              <h1>Review Board</h1>
              <span className="review-board-open-count">
                {totals.open_issues.toLocaleString()} open
              </span>
            </div>
            <p>Review and resolve feedback across shared assets.</p>
            <span className="review-board-secondary-summary">{secondarySummary}</span>
          </div>
          <button
            type="button"
            className="review-board-refresh"
            onClick={() => void reload()}
            disabled={loading}
          >
            <ReviewBoardIcon name="refresh" />
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </header>

        <section className="review-board-stats" aria-label="Review Board statistics">
          {[
            ["Open issues", totals.open_issues, "open"],
            ["Resolved", totals.resolved_issues, "resolved"],
            ["Affected assets", totals.assets_with_open_issues, "assets"],
            ["Resolution rate", `${totals.resolution_rate}%`, "rate"],
          ].map(([label, value, tone]) => (
            <article key={String(label)} className={`review-board-stat ${tone}`}>
              <small>{label}</small>
              <strong>{typeof value === "number" ? value.toLocaleString() : value}</strong>
            </article>
          ))}
        </section>

        <section className="review-board-filters" aria-label="Review Board filters">
          <div className="review-board-filter-main">
            <label>
              <span>Status</span>
              <select
                value={filters.status}
                onChange={(event) =>
                  updateFilter("status", event.target.value as BoardFilters["status"])
                }
              >
                <option value="open">Open</option>
                <option value="resolved">Resolved</option>
                <option value="all">All issues</option>
              </select>
            </label>
            <label className="review-board-reviewer-filter">
              <span>Reviewer</span>
              <input
                value={filters.reviewer}
                placeholder="Reviewer name"
                onChange={(event) => updateFilter("reviewer", event.target.value)}
              />
            </label>
            <label>
              <span>Pinned</span>
              <select
                value={filters.pinned}
                onChange={(event) =>
                  updateFilter("pinned", event.target.value as BoardFilters["pinned"])
                }
              >
                <option value="">All</option>
                <option value="true">Pinned</option>
                <option value="false">Unpinned</option>
              </select>
            </label>
            <label>
              <span>Sort</span>
              <select
                value={filters.sort}
                onChange={(event) =>
                  updateFilter("sort", event.target.value as BoardFilters["sort"])
                }
              >
                <option value="newest">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="recently_updated">Recently updated</option>
                <option value="recently_resolved">Recently resolved</option>
              </select>
            </label>
          </div>

          <details className="review-board-filter-more">
            <summary>
              <ReviewBoardIcon name="filter" />
              Date filters
            </summary>
            <div>
              <label>
                <span>From</span>
                <input
                  type="date"
                  value={inputDate(filters.created_from)}
                  onChange={(event) =>
                    updateFilter(
                      "created_from",
                      event.target.value ? `${event.target.value}T00:00:00Z` : "",
                    )
                  }
                />
              </label>
              <label>
                <span>To</span>
                <input
                  type="date"
                  value={inputDate(filters.created_to)}
                  onChange={(event) =>
                    updateFilter(
                      "created_to",
                      event.target.value ? `${event.target.value}T23:59:59Z` : "",
                    )
                  }
                />
              </label>
            </div>
          </details>
        </section>

        {error && (
          <p className="review-board-error" role="alert">
            {error}
          </p>
        )}

        <section className="review-board-workspace">
          <section className="review-board-inbox" aria-label="Review issues">
            <header>
              <div>
                <small>ISSUE INBOX</small>
                <strong>
                  {page?.total.toLocaleString() || "0"} {page?.total === 1 ? "issue" : "issues"}
                </strong>
              </div>
              {loading && <span className="review-board-inline-loading">Updating…</span>}
            </header>

            <div className="review-board-list">
              {loading && !page ? (
                <div className="review-board-list-skeleton" aria-label="Loading issues">
                  {Array.from({ length: 6 }, (_, index) => (
                    <i key={index} />
                  ))}
                </div>
              ) : !page?.items.length ? (
                <div className="review-board-list-empty">
                  <ReviewBoardIcon name="comment" />
                  <strong>
                    {filters.status === "open"
                      ? "No open review issues"
                      : "No matching review issues"}
                  </strong>
                  <p>
                    {filters.status === "open"
                      ? "New shared-asset feedback will appear here."
                      : "Adjust the filters to broaden the result set."}
                  </p>
                </div>
              ) : (
                page.items.map((issue) => (
                  <ReviewIssueRow
                    key={issue.id}
                    issue={issue}
                    selected={selectedId === issue.id}
                    onSelect={() => setSelectedId(issue.id)}
                  />
                ))
              )}
            </div>

            {page && (
              <footer className="review-board-pagination">
                <span>
                  {resultStart.toLocaleString()}–{resultEnd.toLocaleString()} of{" "}
                  {page.total.toLocaleString()}
                </span>
                <div>
                  <button
                    type="button"
                    disabled={filters.page <= 1}
                    onClick={() => updateFilter("page", filters.page - 1)}
                    aria-label="Previous issue page"
                  >
                    ‹
                  </button>
                  <span>
                    {page.page} / {pageCount}
                  </span>
                  <button
                    type="button"
                    disabled={filters.page * filters.page_size >= page.total}
                    onClick={() => updateFilter("page", filters.page + 1)}
                    aria-label="Next issue page"
                  >
                    ›
                  </button>
                </div>
                <label>
                  <span className="sr-only">Rows per page</span>
                  <select
                    value={filters.page_size}
                    onChange={(event) =>
                      updateFilter(
                        "page_size",
                        Number(event.target.value) as BoardFilters["page_size"],
                      )
                    }
                  >
                    {[25, 50, 100].map((value) => (
                      <option key={value} value={value}>
                        {value} / page
                      </option>
                    ))}
                  </select>
                </label>
              </footer>
            )}
          </section>

          <ReviewAssetPreview detail={detail} loading={detailLoading} />
          <ReviewIssueInspector
            detail={detail}
            loading={detailLoading}
            canResolve={canResolve}
            mutating={mutating}
            mutationError={mutationError}
            onTransition={() => void transition()}
          />
        </section>
      </section>
    </main>
  );
}
