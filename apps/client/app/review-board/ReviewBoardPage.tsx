import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import { BrandIcon } from "../components/Icons";
import { WorkspaceNavigation } from "../components/WorkspaceNavigation";
import { RichAnnotation } from "../public-review/RichAnnotation";
import { fetchBoardIssue, fetchBoardIssues, fetchBoardStats, reopenBoardIssue, resolveBoardIssue } from "./api";
import type { BoardFilters, BoardIssueDetail, BoardPage, BoardStats } from "./types";

const initialFilters: BoardFilters = { status: "open", reviewer: "", pinned: "", created_from: "", created_to: "", sort: "newest", page: 1, page_size: 25 };
const time = (value: string | null) => { if (!value) return "—"; const date = new Date(value); return Number.isFinite(date.valueOf()) ? date.toLocaleString() : "Not available"; };

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
    setLoading(true); setError("");
    try { const [nextPage, nextStats] = await Promise.all([fetchBoardIssues(filters), fetchBoardStats()]); setPage(nextPage); setStats(nextStats); if (selectedId && !nextPage.items.some(item => item.id === selectedId)) { setSelectedId(null); setDetail(null); } }
    catch { setError("Review Board data is unavailable. Refresh and try again."); }
    finally { setLoading(false); }
  }, [canRead, filters, selectedId]);
  useEffect(() => { let alive = true; fetchAccessIdentity().then(identity => { if (alive) setPermissions(identity.permissions); }).catch(() => { if (alive) setIdentityError(true); }); return () => { alive = false; }; }, []);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { if (!selectedId || !canRead) return; let alive = true; setDetailLoading(true); fetchBoardIssue(selectedId).then(value => { if (alive) setDetail(value); }).catch(() => { if (alive) { setDetail(null); setError("The selected issue is unavailable."); } }).finally(() => { if (alive) setDetailLoading(false); }); return () => { alive = false; }; }, [selectedId, canRead]);
  const updateFilter = <K extends keyof BoardFilters>(key: K, value: BoardFilters[K]) => setFilters(current => ({ ...current, [key]: value, page: key === "page" ? Number(value) : 1 }));
  const transition = async () => { if (!detail || !canResolve || mutating) return; setMutating(true); setMutationError(""); try { const transitionResult = detail.status === "open" ? await resolveBoardIssue(detail.id) : await reopenBoardIssue(detail.id); setDetail(current => current ? { ...current, ...transitionResult } : current); await reload(); } catch { setMutationError("The status was not changed. Try again."); } finally { setMutating(false); } };
  const totals = stats || { total_issues: 0, open_issues: 0, resolved_issues: 0, resolution_rate: 0, assets_with_open_issues: 0, shares_with_open_issues: 0 };
  if (permissions === null && !identityError) return <main className="review-board-state" aria-busy="true">Loading your Review Board access…</main>;
  if (identityError) return <main className="review-board-state" role="alert">Review Board is unavailable because your authenticated identity could not be loaded.</main>;
  if (!canRead) return <main className="review-board-state" role="alert">You are signed in, but do not have permission to view the Review Board.</main>;
  return <main className="review-board-shell">
    <aside className="ops-sidebar"><div className="brand"><b><BrandIcon /></b><span><strong>Creative assets</strong><small>Review operations</small></span></div><WorkspaceNavigation active="review-board" showReviewBoard={canRead} /><small className="ops-sidebar-note">Review and resolve shared asset feedback.</small></aside>
    <section className="review-board-main" aria-busy={loading}>
      <header className="review-board-header"><div><small>PUBLIC REVIEW</small><h1>Review Board</h1><p>Authenticated review issues across shared assets.</p></div><button type="button" onClick={() => void reload()} disabled={loading}>Refresh</button></header>
      <section className="review-board-stats" aria-label="Review Board statistics">{[["Total issues", totals.total_issues], ["Open", totals.open_issues], ["Resolved", totals.resolved_issues], ["Resolution rate", `${totals.resolution_rate}%`], ["Assets with open issues", totals.assets_with_open_issues], ["Shares with open issues", totals.shares_with_open_issues]].map(([label, value]) => <article key={String(label)}><small>{label}</small><strong>{value}</strong></article>)}</section>
      <section className="review-board-filters" aria-label="Review Board filters"><label>Status<select value={filters.status} onChange={event => updateFilter("status", event.target.value as BoardFilters["status"])}><option value="open">Open</option><option value="resolved">Resolved</option><option value="all">All</option></select></label><label>Reviewer<input value={filters.reviewer} onChange={event => updateFilter("reviewer", event.target.value)} /></label><label>Pinned<select value={filters.pinned} onChange={event => updateFilter("pinned", event.target.value as BoardFilters["pinned"])}><option value="">All</option><option value="true">Pinned</option><option value="false">Unpinned</option></select></label><label>From<input type="date" value={filters.created_from} onChange={event => updateFilter("created_from", event.target.value ? `${event.target.value}T00:00:00Z` : "")} /></label><label>To<input type="date" value={filters.created_to} onChange={event => updateFilter("created_to", event.target.value ? `${event.target.value}T23:59:59Z` : "")} /></label><label>Sort<select value={filters.sort} onChange={event => updateFilter("sort", event.target.value as BoardFilters["sort"])}><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="recently_updated">Recently updated</option><option value="recently_resolved">Recently resolved</option></select></label><label>Rows<select value={filters.page_size} onChange={event => updateFilter("page_size", Number(event.target.value) as BoardFilters["page_size"])}>{[25,50,100].map(value => <option key={value} value={value}>{value}</option>)}</select></label></section>
      {error && <p className="review-board-error" role="alert">{error}</p>}
      <section className="review-board-content"><div className="review-board-list" aria-label="Review issues">{loading ? <p>Loading issues…</p> : !page?.items.length ? <p>{filters.status === "open" ? "No open review issues." : "No review issues match these filters."}</p> : page.items.map(issue => <button type="button" key={issue.id} className={selectedId === issue.id ? "review-board-row selected" : "review-board-row"} onClick={() => setSelectedId(issue.id)} aria-pressed={selectedId === issue.id}><span aria-label={issue.status === "resolved" ? "Resolved" : "Open"}>{issue.status === "resolved" ? "✓" : "○"}</span><div><strong title={issue.asset.filename || undefined}>{issue.asset.filename || "Untitled asset"}</strong><p>{issue.annotation_preview}</p><small>{issue.reviewer.display_name} · {issue.share.name} · {issue.reply_count} replies {issue.anchor_x !== null ? "· Pinned" : ""}</small></div></button>)}{page && <div className="review-board-pagination"><button disabled={filters.page <= 1} onClick={() => updateFilter("page", filters.page - 1)}>Previous</button><span>Page {page.page} of {Math.max(1, Math.ceil(page.total / page.page_size))}</span><button disabled={filters.page * filters.page_size >= page.total} onClick={() => updateFilter("page", filters.page + 1)}>Next</button></div>}</div>
      <aside className="review-board-detail" aria-live="polite">{detailLoading ? <p>Loading issue…</p> : !detail ? <p>Select an issue to read its full annotation and replies.</p> : <><h2>{detail.asset.filename || "Untitled asset"}</h2><p className="review-board-preview-unavailable">Authenticated preview is unavailable for this source. Annotation details remain available.</p><dl><div><dt>Reviewer</dt><dd>{detail.reviewer.display_name}</dd></div><div><dt>Share</dt><dd>{detail.share.name}</dd></div><div><dt>Status</dt><dd>{detail.status}</dd></div><div><dt>Created</dt><dd>{time(detail.created_at)}</dd></div>{detail.resolved_at && <div><dt>Resolved</dt><dd>{time(detail.resolved_at)} by {detail.resolver?.actor_id || "Unknown"}</dd></div>}</dl><RichAnnotation document={detail.content_json} /><section><h3>Replies ({detail.replies.length})</h3>{detail.replies.length ? detail.replies.map(reply => <article key={reply.id} className="review-board-reply"><strong>{reply.reviewer.display_name}</strong><RichAnnotation document={reply.content_json} /><small>{time(reply.created_at)}</small></article>) : <p>No replies.</p>}</section>{canResolve && <button type="button" className="review-board-transition" disabled={mutating} onClick={() => void transition()}>{mutating ? "Updating…" : detail.status === "open" ? "Mark Done" : "Reopen"}</button>}{mutationError && <p className="review-board-error" role="alert">{mutationError}</p>}</>}</aside></section>
    </section>
  </main>;
}
