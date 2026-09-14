import { describe, expect, it } from "vitest";
import { DEFAULT_VISUAL_SEARCH_SCOPE, committedVisualQuery, normalizeCrop, visualErrorMessage } from "./useVisualSearch";

describe("visual search client helpers", () => {
  it("clamps normalized crop inside image bounds", () => {
    expect(normalizeCrop({ x: 0.9, y: -1, width: 0.8, height: 2 })).toEqual({
      x: 0.9, y: 0, width: 0.09999999999999998, height: 1,
    });
  });

  it("uses controlled API detail messages", () => {
    expect(visualErrorMessage({ detail: { message: "Visual search is busy." } })).toBe("Visual search is busy.");
  });

  it("snapshots normalized crop, text, and scope for a committed request", () => {
    expect(committedVisualQuery({ x: 0.9, y: 0, width: 0.8, height: 1 }, " outdoor ", "source", "google-drive", "source-a", null)).toEqual({
      crop: { x: 0.9, y: 0, width: 0.09999999999999998, height: 1 },
      text: "outdoor", scope: "source", provider: "google-drive", externalSourceId: "source-a", folderId: null,
    });
  });

  it("defaults eligible searches to all resources", () => { expect(DEFAULT_VISUAL_SEARCH_SCOPE).toBe("all"); });

  it("omits explorer source for all resources", () => { expect(committedVisualQuery(undefined, "", "all", "google-drive", "source-a", "folder-a")).toMatchObject({ scope: "all", provider: null, externalSourceId: null, folderId: null }); });

  it("commits the selected folder only for folder scope", () => { expect(committedVisualQuery(undefined, "", "folder", "google-drive", "source-a", "folder-a")).toMatchObject({ scope: "folder", provider: "google-drive", externalSourceId: "source-a", folderId: "folder-a" }); });

});
