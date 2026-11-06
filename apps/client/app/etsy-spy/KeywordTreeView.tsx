import { useEffect, useMemo, useState } from "react";
import { spyFetch, type Keyword, type Page } from "./api";
import {
  createKeywordForest, fetchKeywordPages, filterKeywordForest,
  visibleKeywordRows, type KeywordVisibleRow,
} from "./keywordTree";

type Props = {
  sessionId: string;
  search: string;
  status: string;
  sort: string;
  direction: "asc" | "desc";
  refresh: number;
  onSort: (column: string) => void;
};

const TREE_COLUMNS = [
  { id: "keyword", title: "Keyword hierarchy" },
  { id: "depth", title: "Depth" },
  { id: "priority", title: "Priority" },
  { id: "status", title: "Status" },
  { id: "matchedCount", title: "Listings" },
];

function KeywordTreeRow({
  row, onToggle,
}: {
  row: KeywordVisibleRow;
  onToggle: (key: string) => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const { node, level, hasChildren, isOpen } = row;
  const keyword = node.item.keyword;
  return <tr className={"etsy-tree-row" + (level === 0 ? " is-root" : "") +
    (node.missingParent ? " is-unavailable" : "")} aria-level={level + 1}>
    <td className="etsy-tree-keyword-col">
      <div className="etsy-tree-cell" style={{ paddingInlineStart: `min(${Math.min(level, 20) * 19 + 8}px, var(--etsy-tree-indent-limit, 999px))` }}>
        {level > 0 && <span aria-hidden="true" className="etsy-tree-connector">└</span>}
        {hasChildren
          ? <button type="button" className="etsy-tree-toggle"
              aria-expanded={isOpen}
              aria-label={(isOpen ? "Collapse " : "Expand ") + keyword}
              title={(isOpen ? "Collapse " : "Expand ") + keyword}
              onClick={() => onToggle(node.key)}>
              <span aria-hidden="true" className={"etsy-tree-chevron" + (isOpen ? " open" : "")}>▸</span>
            </button>
          : <span className="etsy-tree-leaf" aria-hidden="true">•</span>}
        <div className="etsy-tree-copy">
          <div className="etsy-tree-keyword-line">
            <strong title={keyword}>{keyword}</strong>
            {hasChildren && <span className="etsy-tree-child-count" title="Direct children">{node.children.length}</span>}
          </div>
          {node.missingParent
            ? <small className="etsy-tree-path-note">Parent record unavailable in synced data</small>
            : <>
              {level === 0 && <small className="etsy-tree-path-note">Session {node.item.sessionId || "unknown"}</small>}
              {node.item.reason && <button className="etsy-tree-evidence-btn" type="button"
                aria-expanded={showEvidence} onClick={() => setShowEvidence(v => !v)}>
                Evidence {showEvidence ? "−" : "+"}
              </button>}
              {node.item.sourceUrl && <a className="etsy-tree-source-link"
                href={node.item.sourceUrl} target="_blank" rel="noopener noreferrer">Source ↗</a>}
            </>}
        </div>
      </div>
      {showEvidence && node.item.reason && <pre className="etsy-evidence etsy-tree-evidence">{node.item.reason}
        {node.item.evidence != null ? "\n" + JSON.stringify(node.item.evidence, null, 2) : null}
      </pre>}
    </td>
    <td className="etsy-tree-compact">{node.missingParent ? "—" : node.item.depth ?? "—"}</td>
    <td className="etsy-num">{node.missingParent ? "—" : node.item.priority ?? "—"}</td>
    <td>{node.missingParent ? <span className="etsy-pill etsy-tree-status missing">Unsynced parent</span>
      : <span className={"etsy-pill etsy-tree-status " + (node.item.status || "pending").toLowerCase()}>
        {node.item.status || "pending"}
      </span>}</td>
    <td className="etsy-num">{node.missingParent ? "—" : node.item.matchedCount ?? "—"}</td>
  </tr>;
}

export function KeywordTreeView({
  sessionId, search, status, sort, direction, refresh, onSort,
}: Props) {
  const requestKey = JSON.stringify([sessionId, refresh]);
  const [result, setResult] = useState<{
    key: string; items: Keyword[]; total: number; truncated: boolean;
  } | null>(null);
  const [requestError, setRequestError] = useState("");
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const abort = new AbortController();
    let alive = true;
    setLoading(true);
    setRequestError("");
    const fetchPage = (page: number, size: number) => {
      const query = new URLSearchParams({ page: String(page), page_size: String(size),
        sort: "updatedAt", dir: "desc" });
      if (sessionId && sessionId !== "all") query.set("session_id", sessionId);
      return spyFetch<Page<Keyword>>("/keywords?" + query.toString(), { signal: abort.signal });
    };
    fetchKeywordPages(fetchPage).then(data => {
      if (alive) { setResult({ key: requestKey, ...data }); setLoading(false); }
    }).catch(err => {
      if (alive) {
        setRequestError(err instanceof Error ? err.message : "Could not load keyword tree");
        setLoading(false);
      }
    });
    return () => { alive = false; abort.abort(); };
  }, [requestKey, sessionId]);

  const current = result?.key === requestKey ? result : null;
  const forest = useMemo(() => createKeywordForest(current?.items || [], sort, direction),
    [current?.items, sort, direction]);
  const projection = useMemo(() => filterKeywordForest(forest, search, status),
    [forest, search, status]);

  useEffect(() => {
    if (!current) return;
    // Show a couple of real branches on first load; leave others collapsed.
    setExpanded(new Set(forest.roots.filter(key => forest.nodes.get(key)?.children.length).slice(0, 2)));
  }, [current?.key]);

  useEffect(() => {
    if (!current || !projection.filtered || !projection.ancestors.size) return;
    // Search matches deep in the tree must remain discoverable, including their parents.
    setExpanded(old => {
      const next = new Set(old);
      for (const key of projection.ancestors) next.add(key);
      return next;
    });
  }, [current?.key, projection]);

  const visible = useMemo(() => visibleKeywordRows(forest, projection, expanded),
    [forest, projection, expanded]);
  const toggleNode = (key: string) => setExpanded(old => {
    const next = new Set(old);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const expandable = useMemo(() =>
    [...forest.nodes.values()].filter(node => node.children.length &&
      projection.visible.has(node.key)).map(node => node.key),
  [forest, projection]);
  const valid = Boolean(current);
  const showLoading = !valid && (loading || !requestError);
  return <>
    <div className="etsy-tree-toolbar" aria-label="Keyword tree controls">
      <span className="etsy-tree-summary">
        {valid ? <>
          <strong>{forest.realCount.toLocaleString()}</strong> keyword nodes
          <span className="etsy-tree-summary-divider">·</span>
          <strong>{forest.roots.length.toLocaleString()}</strong> root branches
          {projection.filtered && <>
            <span className="etsy-tree-summary-divider">·</span>
            <strong>{projection.matchedCount.toLocaleString()}</strong> matches
          </>}
          <span className="etsy-tree-summary-divider">·</span>
          {visible.length.toLocaleString()} visible rows
        </> : "Loading complete keyword hierarchy…"}
      </span>
      <div className="etsy-tree-actions">
        <button type="button" disabled={!valid || !expandable.length}
          onClick={() => setExpanded(new Set(expandable))}>Expand all</button>
        <button type="button" disabled={!valid || !expandable.length}
          onClick={() => setExpanded(new Set())}>Collapse all</button>
      </div>
    </div>
    {valid && (current?.truncated || forest.missingParents > 0) && <div className="etsy-tree-notice" role="status">
      {current?.truncated && <span>Showing {forest.realCount.toLocaleString()} of {current.total.toLocaleString()} nodes. Select a session to narrow the tree. </span>}
      {forest.missingParents > 0 && <span>{forest.missingParents.toLocaleString()} parent records are not synced; placeholder branches keep their children visible.</span>}
    </div>}
    <div className="etsy-table-card etsy-tree-table-card">
      {showLoading ? <div role="status" className="etsy-skeleton">Loading all keyword pages for the tree…</div>
        : requestError ? <div role="alert" className="etsy-error">Cannot load keyword tree: {requestError}</div>
        : !visible.length ? <div className="etsy-empty">
            {projection.filtered ? "No keyword nodes match the current search/status filters." : "No keyword nodes available."}
          </div>
        : <div className="etsy-table-wrap etsy-tree-table-wrap">
          <table className="etsy-data-table is-keyword-tree" aria-label="Expandable keyword hierarchy">
            <thead><tr>{TREE_COLUMNS.map(col => <th key={col.id}
              aria-sort={sort === col.id ? (direction === "asc" ? "ascending" : "descending") : undefined}>
              <button type="button" onClick={() => onSort(col.id)} aria-label={"Sort by " + col.title}>
                {col.title}{sort === col.id ? (direction === "asc" ? " ↑" : " ↓") : ""}
              </button>
            </th>)}</tr></thead>
            <tbody>{visible.map(row => <KeywordTreeRow key={row.node.key} row={row} onToggle={toggleNode} />)}</tbody>
          </table>
        </div>}
    </div>
  </>;
}
