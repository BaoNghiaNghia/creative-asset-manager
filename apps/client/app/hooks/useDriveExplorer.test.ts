import { describe, expect, it } from "vitest";
import {
  EXPLORER_LOCATION_MAX_AGE_MS,
  apiErrorMessage,
  appendUniqueFolderPage,
  oauthMessageFor,
  parseSavedExplorerLocation,
  uploadErrorMessage,
} from "./useDriveExplorer";

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


describe("apiErrorMessage", () => {
  it("uses a structured API detail message without stringifying the object", () => {
    expect(apiErrorMessage({
      detail: { code: "viewer_folder_scope_denied", message: "Folder is outside the viewer folder scope." },
    }, "Unable to load folder")).toBe("Folder is outside the viewer folder scope.");
  });

  it("uses the safe fallback when a structured API detail has no message", () => {
    expect(apiErrorMessage({ detail: { code: "unexpected" } }, "Unable to load folder"))
      .toBe("Unable to load folder");
  });
});


describe("parseSavedExplorerLocation", () => {
  it("restores only a valid same-provider folder path", () => {
    expect(parseSavedExplorerLocation(JSON.stringify({ version: 1, saved_at: 1_000, path: [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "application/vnd.google-apps.folder", provider: "google-drive",
    }] }), "google-drive", 1_000 + EXPLORER_LOCATION_MAX_AGE_MS - 1)?.path[0].id).toBe("folder-1");
  });

  it("rejects stale or malformed saved positions", () => {
    expect(parseSavedExplorerLocation("not json", "google-drive")).toBeNull();
    expect(parseSavedExplorerLocation(JSON.stringify({ version: 1, saved_at: 1_000, path: [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "", provider: "sharepoint",
    }] }), "google-drive", 1_001)).toBeNull();
  });

  it("expires saved positions after fifteen minutes and rejects legacy entries without an expiry", () => {
    const path = [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "application/vnd.google-apps.folder", provider: "google-drive",
    }];
    expect(parseSavedExplorerLocation(JSON.stringify({
      version: 1, saved_at: 1_000, path,
    }), "google-drive", 1_000 + EXPLORER_LOCATION_MAX_AGE_MS)).toBeNull();
    expect(parseSavedExplorerLocation(JSON.stringify({ version: 1, path }), "google-drive", 1_001)).toBeNull();
  });
});


describe("appendUniqueFolderPage", () => {
  it("appends the next normal-browse page without duplicating assets", () => {
    const existing = [{ id: "one" }, { id: "two" }] as never[];
    const incoming = [{ id: "two" }, { id: "three" }] as never[];

    expect(appendUniqueFolderPage(existing, incoming).map(item => item.id))
      .toEqual(["one", "two", "three"]);
  });
});
