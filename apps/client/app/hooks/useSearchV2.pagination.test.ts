import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import { buildSearchRequestBody, isCurrentSearchResponse, mergeSearchResults, SEARCH_PAGE_SIZE } from "./useSearchV2";

function asset(id: string, source = "source-a"): Asset {
  return {
    id,
    provider: "google-drive",
    external_source_id: source,
    name: `${id}.jpg`,
    kind: "image",
    mime_type: "image/jpeg",
  };
}

describe("Search result pagination", () => {
  it("uses a bounded first page", () => {
    expect(SEARCH_PAGE_SIZE).toBe(60);
  });

  it("sends facets only with the initial cursor page", () => {
    const firstPage = buildSearchRequestBody(" horse ", "google-drive", { subject: ["horse"] }, null, false, false);
    expect(firstPage).toMatchObject({ query: "horse", limit: 60, include_facets: true });
    expect(firstPage).not.toHaveProperty("offset");
    expect(firstPage).not.toHaveProperty("cursor");

    const nextPage = buildSearchRequestBody("horse", "google-drive", {}, "cursor-2", true, false);
    expect(nextPage).toMatchObject({ limit: 60, cursor: "cursor-2", include_facets: false });
    expect(nextPage).not.toHaveProperty("offset");
  });

  it("appends pages without duplicating an asset", () => {
    const merged = mergeSearchResults(
      [asset("one"), asset("two")],
      [asset("two"), asset("three")],
    );
    expect(merged.map(item => item.id)).toEqual(["one", "two", "three"]);
  });

  it("keeps equal provider IDs from separate sources distinct", () => {
    const merged = mergeSearchResults(
      [asset("shared", "source-a")],
      [asset("shared", "source-b")],
    );
    expect(merged).toHaveLength(2);
  });

  it("rejects stale page responses after the search epoch changes", () => {
    expect(isCurrentSearchResponse(4, 5)).toBe(false);
    expect(isCurrentSearchResponse(5, 5)).toBe(true);
  });
});
