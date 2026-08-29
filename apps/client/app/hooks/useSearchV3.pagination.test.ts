import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import { buildFolderSearchParams, buildSearchRequestBody, DESIGN_TYPE_FILTER_KEY, isCurrentSearchResponse, mergeFolderSearchResults, mergeSearchResults, normalizeAsinFolderQuery, SEARCH_PAGE_SIZE } from "./useSearchV3";

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

  it("sends the design taxonomy through its dedicated backend filter", () => {
    const request = buildSearchRequestBody("dad", "google-drive", {
      subject: ["family"],
      [DESIGN_TYPE_FILTER_KEY]: ["petfull", "peoplefull", "carfull"],
    }, null, false, false);
    expect(request.facets).toEqual({ subject: ["family"] });
    expect(request.design_types).toEqual(["petfull", "peoplefull", "carfull"]);
    expect(request.facets).not.toHaveProperty(DESIGN_TYPE_FILTER_KEY);
  });

  it("recognizes only a complete ASIN as eligible for folder search", () => {
    expect(normalizeAsinFolderQuery(" 4347749385 ")).toBe("4347749385");
    expect(normalizeAsinFolderQuery(" b0gd6h8hyj ")).toBe("B0GD6H8HYJ");
    expect(normalizeAsinFolderQuery("dad")).toBeNull();
    expect(normalizeAsinFolderQuery("Amazon B0GD6H8HYJ")).toBeNull();
    expect(normalizeAsinFolderQuery("123456789")).toBeNull();
  });

  it("builds a tenant-source-scoped folder query for an ASIN", () => {
    const params = buildFolderSearchParams(" 4347749385 ", "google-drive", "source-a");
    expect(params.get("q")).toBe("4347749385");
    expect(params.get("source_provider")).toBe("google-drive");
    expect(params.get("external_source_id")).toBe("source-a");
    expect(params.get("limit")).toBe("50");
  });

  it("places matching ASIN folders before file results", () => {
    const folder: Asset = {
      id: "folder-asin",
      provider: "google-drive",
      external_source_id: "source-a",
      name: "4347749385",
      kind: "folder",
      mime_type: "application/vnd.google-apps.folder",
    };
    expect(mergeFolderSearchResults([folder], [asset("photo")]).map(item => [item.kind, item.name])).toEqual([
      ["folder", "4347749385"],
      ["image", "photo.jpg"],
    ]);
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
