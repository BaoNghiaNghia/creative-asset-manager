import { describe, expect, it } from "vitest";
import {
  keywordNodeKey, createKeywordForest, filterKeywordForest, visibleKeywordRows,
  fetchKeywordPages,
} from "./keywordTree";
import type { Keyword, Page } from "./api";

const record = (id: string, parentId: string | null, sessionId = "one",
  keyword = id, status = "pending"): Keyword => ({
  id, parentId, sessionId, keyword, status,
  depth: parentId ? 1 : 0, priority: 20, reason: null, evidence: null,
  matchedCount: 0, sourceUrl: null,
});
const key = (id: string, session = "one") => keywordNodeKey(session, id);

describe("Etsy Spy full keyword hierarchy", () => {
  it("keeps cross-page parent-child links and expands or collapses the branch", () => {
    const forest = createKeywordForest([
      record("child", "root"), record("grand", "child"), record("root", null),
    ]);
    expect(forest.realCount).toBe(3);
    expect(forest.missingParents).toBe(0);
    expect(forest.roots).toEqual([key("root")]);
    expect(forest.nodes.get(key("root"))?.children).toEqual([key("child")]);
    const view = filterKeywordForest(forest, "", "");
    expect(visibleKeywordRows(forest, view, new Set()).map(row => row.node.item.id)).toEqual(["root"]);
    expect(visibleKeywordRows(forest, view, new Set([key("root")]))
      .map(row => row.node.item.id)).toEqual(["root", "child"]);
    expect(visibleKeywordRows(forest, view, new Set([key("root"), key("child")]))
      .map(row => row.node.item.id)).toEqual(["root", "child", "grand"]);
  });

  it("shows unavailable parents as placeholders and prevents cycles", () => {
    const forest = createKeywordForest([
      record("orphan", "source-seed"), record("second", "source-seed"),
      record("cycle-a", "cycle-b"), record("cycle-b", "cycle-a"),
    ]);
    expect(forest.missingParents).toBe(1);
    expect(forest.realCount).toBe(4);
    expect(forest.nodes.get(key("source-seed"))?.missingParent).toBe(true);
    expect(forest.nodes.get(key("source-seed"))?.children).toHaveLength(2);
    expect(forest.roots).toContain(key("source-seed"));
    expect(forest.roots).toContain(key("cycle-a"));
    expect(forest.roots).toContain(key("cycle-b"));
    expect(visibleKeywordRows(forest, filterKeywordForest(forest, "", ""),
      new Set(forest.nodes.keys()))).toHaveLength(5);
  });

  it("filters nested descendants and keeps their ancestors visible", () => {
    const forest = createKeywordForest([
      record("root", null), record("intermediate", "root"),
      record("target", "intermediate", "one", "Santa moon shirt", "done"),
      record("sibling", "root", "one", "Other", "pending"),
    ]);
    const filtered = filterKeywordForest(forest, "santa", "done");
    expect(filtered.matchedCount).toBe(1);
    expect(filtered.ancestors).toEqual(new Set([key("intermediate"), key("root")]));
    expect(filtered.visible.size).toBe(3);
    expect(visibleKeywordRows(forest, filtered, filtered.ancestors)
      .map(row => row.node.item.id)).toEqual(["root", "intermediate", "target"]);
    expect(filterKeywordForest(forest, "absent", "").matchedCount).toBe(0);
  });

  it("never attaches matching IDs from separate sessions", () => {
    const forest = createKeywordForest([
      record("root", null, "s1"), record("child", "root", "s1"),
      record("root", null, "s2"), record("child", "root", "s2"),
    ]);
    expect(forest.roots).toHaveLength(2);
    expect(forest.nodes.get(key("child", "s1"))?.parentKey).toBe(key("root", "s1"));
    expect(forest.nodes.get(key("child", "s2"))?.parentKey).toBe(key("root", "s2"));
  });

  it("fetches all server pages, not just the first 50/200 rows", async () => {
    const records = Array.from({ length: 215 }, (_, index) =>
      record("node-" + index, index > 0 ? "node-0" : null));
    const pages: number[] = [];
    const result = await fetchKeywordPages(async (page, size): Promise<Page<Keyword>> => {
      pages.push(page);
      return {
        items: records.slice((page - 1) * size, page * size),
        page, page_size: size, total: records.length,
        pages: Math.ceil(records.length / size),
        source_status: "offline", last_synced_at: null,
      };
    });
    expect(pages).toEqual([1, 2]);
    expect(result.items).toHaveLength(215);
    expect(result.total).toBe(215);
    expect(result.truncated).toBe(false);
  });
});
