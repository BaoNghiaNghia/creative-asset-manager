import { describe, expect, it } from "vitest";
import {
  appendKeywordHistory,
  keywordHistoryKey,
  KEYWORD_SEARCH_HISTORY_LIMIT,
  readKeywordHistory,
} from "./keywordSearchHistory";

describe("Stage 0 keyword search history", () => {
  it("isolates tenant and user keys, and never enables history without identity", () => {
    expect(keywordHistoryKey(null, "u")).toBeNull();
    expect(keywordHistoryKey("tenant", null)).toBeNull();
    expect(keywordHistoryKey("tenant-1", "u-1")).not.toBe(keywordHistoryKey("tenant-2", "u-1"));
    expect(keywordHistoryKey("tenant-1", "u-1")).not.toBe(keywordHistoryKey("tenant-1", "u-2"));
    expect(keywordHistoryKey("a:b", "c")).not.toBe(keywordHistoryKey("a", "b:c"));
  });
  it("normalizes, deduplicates, skips tiny queries and limits persisted history", () => {
    expect(appendKeywordHistory(["hotdog cap", "Blue cap"], "  HOTDOG    CAP ")).toEqual(["HOTDOG CAP", "Blue cap"]);
    expect(appendKeywordHistory(["Blue cap"], "A")).toEqual(["Blue cap"]);
    const inputs = Array.from({ length: 30 }, (_, i) => "Keyword " + i);
    expect(readKeywordHistory(JSON.stringify([...inputs, "keyword 0"]))).toHaveLength(KEYWORD_SEARCH_HISTORY_LIMIT);
    expect(readKeywordHistory(JSON.stringify(["", " A ", "Hat", "hat", 2, null]))).toEqual(["Hat"]);
    expect(readKeywordHistory("{")).toEqual([]);
    expect(readKeywordHistory('{"x": 1}')).toEqual([]);
  });
});
