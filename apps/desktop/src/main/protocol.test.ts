import { describe, expect, it } from "vitest";
import { findOAuthDeepLink, parseOAuthDeepLink } from "./protocol";

const ticket = "a".repeat(43);

describe("desktop OAuth deep links", () => {
  it("accepts only the canonical OAuth-complete URL", () => {
    expect(parseOAuthDeepLink("cam://oauth-complete?ticket=" + ticket)).toEqual({ ticket });
  });

  it.each([
    "cam://open-file?ticket=" + ticket,
    "cam://oauth-complete?ticket=" + ticket + "&extra=1",
    "cam://oauth-complete",
    "file:///tmp/x",
    "javascript:alert(1)",
    "cam://oauth-complete?ticket=short",
  ])("rejects %s", (value) => {
    expect(parseOAuthDeepLink(value)).toBeUndefined();
  });

  it("extracts only valid protocol arguments", () => {
    expect(findOAuthDeepLink(["--flag", "cam://run?x=1", "cam://oauth-complete?ticket=" + ticket])).toEqual({ ticket });
  });
});
