import type { Keyword, Page } from "./api";

/** Node identifiers are only unique inside a Windows scouting session. */
export function keywordNodeKey(sessionId: string, id: string): string {
  return JSON.stringify([sessionId, id]);
}

export type KeywordTreeNode = {
  key: string;
  item: Keyword;
  parentKey: string | null;
  children: string[];
  missingParent: boolean;
};

export type KeywordForest = {
  nodes: Map<string, KeywordTreeNode>;
  roots: string[];
  realCount: number;
  missingParents: number;
};

export type KeywordProjection = {
  visible: Set<string>;
  matching: Set<string>;
  ancestors: Set<string>;
  matchedCount: number;
  filtered: boolean;
};

export type KeywordVisibleRow = {
  node: KeywordTreeNode;
  level: number;
  hasChildren: boolean;
  isOpen: boolean;
};

/** Fetch all pages without issuing concurrent requests against the CAM DB. */
export async function fetchKeywordPages(
  fetchPage: (page: number, size: number) => Promise<Page<Keyword>>,
  maximum = 10000,
): Promise<{ items: Keyword[]; total: number; truncated: boolean }> {
  const size = 200;
  const first = await fetchPage(1, size);
  const total = Math.max(0, first.total || 0, first.items.length);
  const items: Keyword[] = [...first.items];
  const allowedPages = Math.ceil(Math.max(1, maximum) / size);
  const totalPages = Math.min(Math.ceil(total / size), allowedPages);
  for (let page = 2; page <= totalPages; page++) {
    const next = await fetchPage(page, size);
    if (!next.items.length) break;
    items.push(...next.items);
  }
  return {
    items: items.slice(0, maximum),
    total,
    truncated: total > maximum || items.length < total,
  };
}

function compareNodes(a: KeywordTreeNode, b: KeywordTreeNode, column: string, direction: "asc" | "desc"): number {
  const order = direction === "asc" ? 1 : -1;
  const aItem = a.item;
  const bItem = b.item;
  const compareText = (x: string | null | undefined, y: string | null | undefined) =>
    (x || "").localeCompare(y || "", undefined, { numeric: true, sensitivity: "base" });
  const compareNumber = (x: number | null | undefined, y: number | null | undefined) =>
    (x ?? Number.NEGATIVE_INFINITY) - (y ?? Number.NEGATIVE_INFINITY);
  let result = 0;
  if (column === "priority") result = compareNumber(aItem.priority, bItem.priority);
  else if (column === "depth") result = compareNumber(aItem.depth, bItem.depth);
  else if (column === "matchedCount") result = compareNumber(aItem.matchedCount, bItem.matchedCount);
  else if (column === "status") result = compareText(aItem.status, bItem.status);
  else if (column === "updatedAt") result = compareText(aItem.updatedAt, bItem.updatedAt);
  else result = compareText(aItem.keyword, bItem.keyword);
  return result * order || compareText(aItem.keyword, bItem.keyword) || compareText(a.key, b.key);
}

/**
 * Preserve the hierarchy across pagination boundaries. Missing parents are
 * displayed as explicitly labeled placeholder nodes, not fabricated keywords.
 * Parent cycles and self-parent references become roots instead of looping.
 */
export function createKeywordForest(
  records: Keyword[], column = "priority", direction: "asc" | "desc" = "desc",
): KeywordForest {
  const nodes = new Map<string, KeywordTreeNode>();
  for (const item of records) {
    if (!item || !item.id) continue;
    const key = keywordNodeKey(item.sessionId || "", item.id);
    if (nodes.has(key)) continue;
    nodes.set(key, { key, item, parentKey: null, children: [], missingParent: false });
  }
  const realCount = nodes.size;
  let missingParents = 0;
  for (const node of [...nodes.values()]) {
    const parentId = node.item.parentId;
    if (!parentId || parentId === node.item.id) continue;
    const parentKey = keywordNodeKey(node.item.sessionId || "", parentId);
    if (nodes.has(parentKey)) continue;
    missingParents++;
    nodes.set(parentKey, {
      key: parentKey,
      item: {
        ...node.item, id: parentId, parentId: null,
        keyword: "Parent not synced · " + parentId,
        depth: node.item.depth != null ? Math.max(0, node.item.depth - 1) : null,
        status: "missing", priority: null, matchedCount: null,
        reason: null, evidence: null, sourceUrl: null,
      },
      parentKey: null, children: [], missingParent: true,
    });
  }
  const roots: string[] = [];
  for (const node of nodes.values()) {
    const parentId = node.item.parentId;
    const parentKey = parentId && parentId !== node.item.id
      ? keywordNodeKey(node.item.sessionId || "", parentId) : null;
    let cycle = false;
    if (parentKey && nodes.has(parentKey)) {
      const seen = new Set<string>([node.key]);
      let cursor: string | null = parentKey;
      while (cursor) {
        if (seen.has(cursor)) { cycle = true; break; }
        seen.add(cursor);
        const parent: KeywordTreeNode | undefined = nodes.get(cursor);
        const next: string | null | undefined = parent?.item.parentId;
        cursor = next && next !== parent?.item.id
          ? keywordNodeKey(parent?.item.sessionId || "", next) : null;
        if (cursor && !nodes.has(cursor)) cursor = null;
      }
    }
    if (!cycle && parentKey && nodes.has(parentKey)) {
      node.parentKey = parentKey;
      nodes.get(parentKey)?.children.push(node.key);
    } else roots.push(node.key);
  }
  const comparator = (x: string, y: string) =>
    compareNodes(nodes.get(x)!, nodes.get(y)!, column, direction);
  roots.sort(comparator);
  for (const node of nodes.values()) node.children.sort(comparator);
  return { nodes, roots, realCount, missingParents };
}

/** A filtered branch includes matching nodes and every parent on their path. */
export function filterKeywordForest(
  forest: KeywordForest, search: string, status: string,
): KeywordProjection {
  const q = search.trim().toLocaleLowerCase();
  const filtered = Boolean(q || status);
  const visible = new Set<string>();
  const matching = new Set<string>();
  const ancestors = new Set<string>();
  for (const node of forest.nodes.values()) {
    if (node.missingParent) continue;
    if (q && !node.item.keyword.toLocaleLowerCase().includes(q)) continue;
    if (status && node.item.status !== status) continue;
    matching.add(node.key);
    let cursor: string | null = node.key;
    while (cursor) {
      if (visible.has(cursor)) break;
      visible.add(cursor);
      const parent: string | null = forest.nodes.get(cursor)?.parentKey || null;
      if (parent) ancestors.add(parent);
      cursor = parent;
    }
  }
  if (!filtered) {
    for (const key of forest.nodes.keys()) visible.add(key);
  }
  return { visible, matching, ancestors, matchedCount: matching.size, filtered };
}

/** Flatten only expanded branches, keeping DOM size bounded for large trees. */
export function visibleKeywordRows(
  forest: KeywordForest, projection: KeywordProjection, expanded: ReadonlySet<string>,
): KeywordVisibleRow[] {
  const rows: KeywordVisibleRow[] = [];
  const visit = (key: string, level: number) => {
    if (!projection.visible.has(key)) return;
    const node = forest.nodes.get(key);
    if (!node) return;
    const children = node.children.filter(child => projection.visible.has(child));
    const isOpen = children.length > 0 && expanded.has(key);
    rows.push({ node, level, hasChildren: children.length > 0, isOpen });
    if (isOpen) for (const child of children) visit(child, level + 1);
  };
  for (const key of forest.roots) visit(key, 0);
  return rows;
}
