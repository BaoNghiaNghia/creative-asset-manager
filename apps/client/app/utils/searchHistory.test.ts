import { describe, expect, it } from "vitest";
import { SEARCH_HISTORY_LIMIT, addSearchHistory, readSearchHistory } from "./searchHistory";

describe("search history", () => {
  it("keeps the newest normalized query first and de-duplicates case-insensitively", () => {
    expect(addSearchHistory(["Baby onesie", "airport"], "  AIRPORT  ")).toEqual(["AIRPORT", "Baby onesie"]);
  });

  it("reads only valid unique entries and limits storage", () => {
    const values = Array.from({ length: SEARCH_HISTORY_LIMIT + 3 }, (_, index) => "query " + index);
    expect(readSearchHistory(JSON.stringify(["  ", "Query 0", "query 0", ...values]))).toHaveLength(SEARCH_HISTORY_LIMIT);
    expect(readSearchHistory("invalid")).toEqual([]);
  });
});
