import { describe, expect, it } from "vitest";
import { preferredChromeProfile } from "./browserLauncher";

describe("preferred Chrome OAuth profile", () => {
  it("prefers the most recently active Chrome profile", () => {
    expect(preferredChromeProfile({
      profile: {
        last_used: "Profile 2",
        last_active_profiles: ["Profile 7", "Profile 2"],
      },
    })).toBe("Profile 7");
  });

  it("falls back to Chrome last_used", () => {
    expect(preferredChromeProfile({ profile: { last_used: "Default" } })).toBe("Default");
  });

  it("rejects unsafe profile directory values", () => {
    expect(preferredChromeProfile({ profile: { last_used: "../Profile 1" } })).toBeUndefined();
    expect(preferredChromeProfile({ profile: { last_active_profiles: ["Profile\\evil"] } })).toBeUndefined();
  });
});
