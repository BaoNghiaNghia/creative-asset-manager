import { describe, expect, it } from "vitest";
import { createKeywordForest } from "./keywordTree";
import {
  normalizeKeywordPhrase, createKeywordGroupForest, filterKeywordGroupForest,
  visibleKeywordGroups,
} from "./keywordMindmap";
import type { Keyword } from "./api";

function record(id: string, parentId: string | null, sessionId: string,
  keyword: string, status = "done", matchedCount = 0): Keyword {
  return {
    id, parentId, sessionId, keyword, status, matchedCount,
    depth: parentId ? 1 : 0, priority: 50,
    reason: null, evidence: null, sourceUrl: null,
  };
}

describe("Etsy Spy grouped Mindmap", () => {
  it("normalizes only safe text differences and preserves actual keyword meaning", () => {
    expect(normalizeKeywordPhrase("  SANTA   Claus\tRUDOLPH  ")).toBe("santa claus rudolph");
    expect(normalizeKeywordPhrase("Ｓａｎｔａ Claus")).toBe("santa claus");
    expect(normalizeKeywordPhrase("Santa Claus t-shirt")).not.toBe(
      normalizeKeywordPhrase("Santa Claus shirt"));
  });

  it("merges repeated roots across sessions, retains each Scout ID and joins matching descendants", () => {
    const raw = createKeywordForest([
      record("root-1", null, "s1", " Santa  Claus  Rudolph boop shirt", "done", 6),
      record("child-1", "root-1", "s1", "Rudolph Funny", "done", 4),
      record("root-2", null, "s2", "SANTA CLAUS RUDOLPH BOOP SHIRT", "exploring", 5),
      record("child-2", "root-2", "s2", "rudolph funny", "exploring", 7),
      record("root-3", null, "s3", "Santa Claus Rudolph boop shirt", "done", 9),
      record("other", null, "s3", "Pet Vintage Bootleg", "failed", 0),
    ]);
    const grouped = createKeywordGroupForest(raw);
    expect(grouped.rawCount).toBe(6);
    expect(grouped.rootRecords).toBe(4);
    expect(grouped.roots).toHaveLength(2);
    expect(grouped.realGroups).toBe(3);
    expect(grouped.mergedRecords).toBe(3);
    const root = [...grouped.nodes.values()].find(item =>
      item.normalized === "santa claus rudolph boop shirt")!;
    expect(root.members).toHaveLength(3);
    expect(root.sessions).toEqual(["s1", "s2", "s3"]);
    expect(root.status).toBe("mixed");
    expect(root.statusCounts).toEqual({ done: 2, exploring: 1 });
    expect(root.matchedCount).toBe(9); // Do not sum (9+6+5)!
    expect(root.children).toHaveLength(1);
    const child = grouped.nodes.get(root.children[0])!;
    expect(child.members).toHaveLength(2);
    expect(child.parentKey).toBe(root.key);
    expect(root.branchRecords).toBe(5);
    expect(raw.nodes.size).toBe(6); // Original provenance remains untouched.
  });

  it("never merges identical keywords under unrelated parents", () => {
    const raw = createKeywordForest([
      record("root-a", null, "s1", "Merry Xmas"),
      record("root-b", null, "s2", "Halloween"),
      record("child-a", "root-a", "s1", "Funny shirt"),
      record("child-b", "root-b", "s2", "FUNNY shirt"),
    ]);
    const grouped = createKeywordGroupForest(raw);
    const children = [...grouped.nodes.values()].filter(node =>
      node.normalized === "funny shirt");
    expect(children).toHaveLength(2);
    expect(children[0].parentKey).not.toBe(children[1].parentKey);
    expect(grouped.distinctPhrases).toBe(3);
    expect(grouped.mergedRecords).toBe(0);
  });

  it("shows matching ancestors and searches mixed-status member groups", () => {
    const raw = createKeywordForest([
      record("root", null, "s1", "Root", "done"),
      record("branch", "root", "s1", "Rudolph", "failed"),
      record("leaf", "branch", "s1", "Christmas boop", "exploring"),
      record("root-other", null, "s2", "Other", "pending"),
      record("root-2", null, "s3", "root", "exploring"),
    ]);
    const grouped = createKeywordGroupForest(raw);
    const filter = filterKeywordGroupForest(grouped, "Christmas", "exploring");
    expect(filter.matchedCount).toBe(1);
    expect(filter.visible.size).toBe(3);
    expect(filter.ancestors.size).toBe(2);
    const visible = visibleKeywordGroups(grouped, filter, filter.ancestors);
    expect(visible.rows.map(row => row.group.keyword)).toEqual([
      "Root", "Rudolph", "Christmas boop",
    ]);
    expect(filterKeywordGroupForest(grouped, "", "failed").matchedCount).toBe(1);
    expect(filterKeywordGroupForest(grouped, "absent", "").matchedCount).toBe(0);
  });

  it("preserves missing-parent placeholders without cross-session false grouping", () => {
    const raw = createKeywordForest([
      record("one", "missing-parent", "s1", "Design A"),
      record("two", "missing-parent", "s2", "Design B"),
    ]);
    const grouped = createKeywordGroupForest(raw);
    expect(grouped.missingParents).toBe(2);
    expect(grouped.roots).toHaveLength(2);
    expect(grouped.realGroups).toBe(2);
    expect([...grouped.nodes.values()].filter(node => node.missingParent)).toHaveLength(2);
    expect(grouped.mergedRecords).toBe(0);
  });

  it("keeps large maps bounded and permits focusing a branch without losing data", () => {
    const records: Keyword[] = [];
    for (let i = 0; i < 737; i++) {
      records.push(record("node-" + i, i < 2 ? null : i % 2 ? "node-1" : "node-0",
        "s1", i < 2 ? "Root " + i : "Design " + i, "pending"));
    }
    const raw = createKeywordForest(records);
    const grouped = createKeywordGroupForest(raw);
    const all = filterKeywordGroupForest(grouped, "", "");
    const expanded = new Set([...grouped.nodes.keys()]);
    const bounded = visibleKeywordGroups(grouped, all, expanded, { limit: 260 });
    expect(bounded.rows).toHaveLength(260);
    expect(bounded.hidden).toBe(477);
    expect(bounded.rows.length + bounded.hidden).toBe(737);
    const focused = visibleKeywordGroups(grouped, all, expanded, {
      focus: grouped.roots[0], limit: 800,
    });
    expect(focused.rows.length).toBeLessThan(737);
    expect(focused.rows[0].group.keyword).toBe("Root 0");
    expect(grouped.rawCount).toBe(737);
  });
});