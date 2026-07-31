import { describe, expect, it } from "vitest";
import { oauthMessageFor, parseSavedExplorerLocation, uploadErrorMessage } from "./useDriveExplorer";

describe("oauthMessageFor", () => {
  it("explains local bootstrap admission failures", () => {
    expect(oauthMessageFor("self_signup_disabled")).toContain("administrator");
    expect(oauthMessageFor("tenant_membership_required")).toContain("workspace membership");
    expect(oauthMessageFor("account_inactive")).toContain("suspended or disabled");
  });

  it("keeps a safe fallback for unknown callback codes", () => {
    expect(oauthMessageFor("unexpected_internal_code")).toBe(
      "Cloud sign-in could not be completed.",
    );
  });
});


describe("uploadErrorMessage", () => {
  it("keeps the safe API upload failure detail", () => {
    expect(uploadErrorMessage({ detail: "Google Drive write access is required." }))
      .toBe("Google Drive write access is required.");
  });

  it("uses a safe fallback for malformed upload failures", () => {
    expect(uploadErrorMessage(null)).toBe("Upload failed. Try again.");
  });
});


describe("parseSavedExplorerLocation", () => {
  it("restores only a valid same-provider folder path", () => {
    expect(parseSavedExplorerLocation(JSON.stringify({ version: 1, path: [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "application/vnd.google-apps.folder", provider: "google-drive",
    }] }), "google-drive")?.path[0].id).toBe("folder-1");
  });

  it("rejects stale or malformed saved positions", () => {
    expect(parseSavedExplorerLocation("not json", "google-drive")).toBeNull();
    expect(parseSavedExplorerLocation(JSON.stringify({ version: 1, path: [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "", provider: "sharepoint",
    }] }), "google-drive")).toBeNull();
  });
});
