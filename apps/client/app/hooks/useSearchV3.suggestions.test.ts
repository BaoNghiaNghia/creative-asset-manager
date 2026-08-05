import { describe, expect, it } from "vitest";
import {
  isCurrentSuggestionResponse,
  isSuggestionAuthorizationDenial,
  shouldPreserveSuggestionsAfterFailure,
  suggestionQueriesMatch,
} from "./useSearchV3";

describe("Search V3 suggestion isolation", () => {
  it("preserves matching successful suggestions on transient failures", () => {
    for (const status of [429, 502, 503]) {
      expect(shouldPreserveSuggestionsAfterFailure(status, true)).toBe(true);
    }
  });

  it("keeps a 409 isolated from search generation state", () => {
    expect(shouldPreserveSuggestionsAfterFailure(409, true)).toBe(true);
    expect(isSuggestionAuthorizationDenial(409)).toBe(false);
  });

  it("clears suggestions for authorization denial or an unrelated query", () => {
    expect(shouldPreserveSuggestionsAfterFailure(403, true)).toBe(false);
    expect(shouldPreserveSuggestionsAfterFailure(503, false)).toBe(false);
    expect(suggestionQueriesMatch("milo", "winter campaign")).toBe(false);
    expect(suggestionQueriesMatch("milo", "milos")).toBe(true);
  });

  it("rejects a stale response when its external source scope changed", () => {
    expect(isCurrentSuggestionResponse(4, 4, "google-drive:source-a:milo", "google-drive:source-b:milo")).toBe(false);
    expect(isCurrentSuggestionResponse(4, 5, "google-drive:source-a:milo", "google-drive:source-a:milo")).toBe(false);
  });
});
