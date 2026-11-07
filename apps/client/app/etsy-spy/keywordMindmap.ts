import type { KeywordTreeNode, KeywordForest } from "./keywordTree";

/**
 * One visual node represents the same phrase at the same branch path,
 * even when it occurs in many Windows sessions. Raw node IDs are retained
 * as provenance and are NEVER merged in the database.
 */
export function normalizeKeywordPhrase(value: string): string {
  return value.normalize("NFKC")
    .replace(/[\u200b-\u200d\ufeff]/g, "")
    .trim().replace(/\s+/g, " ").toLocaleLowerCase("en-US");
}

export type KeywordGroup = {
  key: string;
  keyword: string;
  normalized: string;
  parentKey: string | null;
  depth: number;
  members: string[];
  children: string[];
  sessions: string[];
  statusCounts: Record<string, number>;
  status: string;
  priority: number | null;
  matchedCount: number | null;
  missingParent: boolean;
  branchRecords: number;
};

export type KeywordGroupForest = {
  nodes: Map<string, KeywordGroup>;
  roots: string[];
  rawCount: number;
  rootRecords: number;
  realGroups: number;
  mergedRecords: number;
  distinctPhrases: number;
  missingParents: number;
};

export type KeywordGroupProjection = {
  visible: Set<string>;
  matching: Set<string>;
  ancestors: Set<string>;
  filtered: boolean;
  matchedCount: number;
};

export type KeywordGroupVisible = {
  group: KeywordGroup;
  level: number;
  hasChildren: boolean;
  isOpen: boolean;
};

export function createKeywordGroupForest(forest: KeywordForest): KeywordGroupForest {
  const nodes = new Map<string, KeywordGroup>();
  const phrases = new Set<string>();

  const visitLayer = (originalKeys: string[], parentKey: string | null, depth: number): string[] => {
    const buckets = new Map<string, KeywordTreeNode[]>();
    for (const originalKey of originalKeys) {
      const original = forest.nodes.get(originalKey);
      if (!original) continue;
      // An absent parent record is a placeholder, not a true keyword.
      // Never conflate missing parent identities from different sessions.
      const normalized = original.missingParent
        ? "missing:" + original.key
        : normalizeKeywordPhrase(original.item.keyword || "");
      if (!original.missingParent) phrases.add(normalized);
      const bucket = buckets.get(normalized) || [];
      bucket.push(original);
      buckets.set(normalized, bucket);
    }

    const keys: string[] = [];
    for (const [normalized, originals] of buckets) {
      const key = JSON.stringify([parentKey, normalized]);
      const statuses: Record<string, number> = {};
      const sessions = new Set<string>();
      let priority: number | null = null;
      let matchedCount: number | null = null;
      const children: string[] = [];
      for (const original of originals) {
        if (!original.missingParent) {
          const status = (original.item.status || "pending").toLowerCase();
          statuses[status] = (statuses[status] || 0) + 1;
          sessions.add(original.item.sessionId || "unknown");
          if (original.item.priority != null)
            priority = Math.max(priority ?? Number.NEGATIVE_INFINITY, original.item.priority);
          if (original.item.matchedCount != null)
            matchedCount = Math.max(matchedCount ?? Number.NEGATIVE_INFINITY, original.item.matchedCount);
        }
        children.push(...original.children);
      }
      const states = Object.keys(statuses);
      const group: KeywordGroup = {
        key, keyword: originals[0].item.keyword,
        normalized, parentKey, depth,
        members: originals.map(item => item.key), children: [],
        sessions: [...sessions].sort(),
        statusCounts: statuses,
        status: originals.every(item => item.missingParent) ? "missing"
          : states.length === 1 ? states[0] : "mixed",
        priority, matchedCount,
        missingParent: originals.every(item => item.missingParent),
        branchRecords: originals.filter(item => !item.missingParent).length,
      };
      nodes.set(key, group);
      group.children = visitLayer(children, key, depth + 1);
      for (const child of group.children)
        group.branchRecords += nodes.get(child)?.branchRecords || 0;
      keys.push(key);
    }
    return keys;
  };

  const roots = visitLayer(forest.roots, null, 0);
  const realGroups = [...nodes.values()].filter(group => !group.missingParent).length;
  return {
    nodes, roots,
    rawCount: forest.realCount,
    rootRecords: forest.roots.length,
    realGroups,
    mergedRecords: Math.max(0, forest.realCount - realGroups),
    distinctPhrases: phrases.size,
    missingParents: forest.missingParents,
  };
}

export function filterKeywordGroupForest(
  forest: KeywordGroupForest, search: string, status: string,
): KeywordGroupProjection {
  const text = normalizeKeywordPhrase(search);
  const state = status.toLowerCase();
  const filtered = Boolean(text || (state && state !== "all"));
  const visible = new Set<string>();
  const matching = new Set<string>();
  const ancestors = new Set<string>();
  if (!filtered) {
    for (const key of forest.nodes.keys()) visible.add(key);
    return { visible, matching, ancestors, matchedCount: 0, filtered };
  }
  for (const group of forest.nodes.values()) {
    if (group.missingParent) continue;
    if (text && !group.normalized.includes(text)) continue;
    if (state && state !== "all" && !group.statusCounts[state]) continue;
    matching.add(group.key);
    let cursor: string | null = group.key;
    while (cursor) {
      if (visible.has(cursor)) break;
      visible.add(cursor);
      const parent: string | null = forest.nodes.get(cursor)?.parentKey || null;
      if (parent) ancestors.add(parent);
      cursor = parent;
    }
  }
  return { visible, matching, ancestors, matchedCount: matching.size, filtered };
}

export function visibleKeywordGroups(
  forest: KeywordGroupForest, projection: KeywordGroupProjection,
  expanded: ReadonlySet<string>, options: { focus?: string | null; limit?: number } = {},
): { rows: KeywordGroupVisible[]; hidden: number } {
  const limit = Math.max(1, options.limit ?? 260);
  const rows: KeywordGroupVisible[] = [];
  let hidden = 0;
  const visited = new Set<string>();
  const walk = (key: string, level: number) => {
    if (visited.has(key) || !projection.visible.has(key)) return;
    visited.add(key);
    const group = forest.nodes.get(key);
    if (!group) return;
    const children = group.children.filter(child => projection.visible.has(child));
    const isOpen = children.length > 0 && expanded.has(key);
    if (rows.length < limit)
      rows.push({ group, level, hasChildren: children.length > 0, isOpen });
    else hidden++;
    if (isOpen) for (const child of children) walk(child, level + 1);
  };
  if (options.focus && forest.nodes.has(options.focus)) walk(options.focus, 0);
  else for (const key of forest.roots) walk(key, 0);
  return { rows, hidden };
}
