import { describe, expect, it } from "vitest";
import { desktopUpdateLabel, desktopUpdateProgress, type UpdateState } from "./DesktopUpdateNotice";

function state(overrides: Partial<UpdateState>): UpdateState {
  return {
    status: "idle",
    currentVersion: "0.1.6",
    ...overrides,
  };
}

describe("DesktopUpdateNotice", () => {
  it("clamps and rounds real download progress", () => {
    expect(desktopUpdateProgress(undefined)).toBe(0);
    expect(desktopUpdateProgress(-12)).toBe(0);
    expect(desktopUpdateProgress(47.6)).toBe(48);
    expect(desktopUpdateProgress(140)).toBe(100);
  });

  it("uses compact status labels for each actionable update state", () => {
    expect(desktopUpdateLabel(state({ status: "checking" }))).toBe("Checking for updates");
    expect(desktopUpdateLabel(state({ status: "available", availableVersion: "0.1.7" }))).toBe("Update 0.1.7 available");
    expect(desktopUpdateLabel(state({ status: "downloading", percent: 42.2 }))).toBe("Downloading 42%");
    expect(desktopUpdateLabel(state({ status: "ready", availableVersion: "0.1.7" }))).toBe("Update ready");
    expect(desktopUpdateLabel(state({ status: "error" }))).toBe("Update failed");
  });
});
