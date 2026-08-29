import { describe, expect, it } from "vitest";
import driveExplorerSource from "./useDriveExplorer.ts?raw";
import {
  apiErrorMessage,
  appendUniqueFolderPage,
  clearSavedExplorerLocation,
  isPureViewerIdentity,
  oauthMessageFor,
  parseSavedExplorerLocation,
  savedLocationIsAuthorized,
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

describe("isPureViewerIdentity", () => {
  it("gates bootstrap to pure viewers without changing operator/admin initialization", () => {
    expect(isPureViewerIdentity({ roles: ["viewer"], is_processing_admin: false })).toBe(true);
    expect(isPureViewerIdentity({ roles: ["viewer", "operator"], is_processing_admin: false })).toBe(false);
    expect(isPureViewerIdentity({ roles: ["tenant_admin"], is_processing_admin: true })).toBe(false);
  });
});


describe("parseSavedExplorerLocation", () => {
  it("restores only a valid same-provider folder path", () => {
    expect(parseSavedExplorerLocation(JSON.stringify({
      version: 3,
      provider: "google-drive",
      external_source_id: "source-1",
      assigned_root_id: "folder-1",
      saved_at: 1_000,
      path: [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "application/vnd.google-apps.folder", provider: "google-drive", external_source_id: "source-1",
    }] }), "google-drive")?.path[0].id).toBe("folder-1");
  });

  it("rejects malformed, legacy and cross-source saved positions", () => {
    expect(parseSavedExplorerLocation("not json", "google-drive")).toBeNull();
    expect(parseSavedExplorerLocation(JSON.stringify({ version: 1, saved_at: 1_000, path: [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "", provider: "google-drive",
    }] }), "google-drive")).toBeNull();
    expect(parseSavedExplorerLocation(JSON.stringify({
      version: 3, provider: "google-drive", external_source_id: "source-1",
      assigned_root_id: "folder-1", saved_at: 1_000, path: [{
        id: "folder-1", name: "Campaign", kind: "folder", mime_type: "folder",
        provider: "google-drive", external_source_id: "source-2",
      }],
    }), "google-drive")).toBeNull();
  });

  it("restores saved positions regardless of how old saved_at is", () => {
    const path = [{
      id: "folder-1", name: "Campaign", kind: "folder", mime_type: "application/vnd.google-apps.folder", provider: "google-drive", external_source_id: "source-1",
    }];
    expect(parseSavedExplorerLocation(JSON.stringify({
      version: 3, provider: "google-drive", external_source_id: "source-1",
      assigned_root_id: "folder-1", saved_at: 1, path,
    }), "google-drive")?.path[0].id).toBe("folder-1");
  });

  it("rejects a location saved by another provider", () => {
    expect(parseSavedExplorerLocation(JSON.stringify({
      version: 3, provider: "google-drive", external_source_id: "source-1",
      assigned_root_id: "folder-1", saved_at: 1, path: [{
        id: "folder-1", name: "Campaign", kind: "folder", mime_type: "folder",
        provider: "google-drive", external_source_id: "source-1",
      }],
    }), "sharepoint")).toBeNull();
  });

  it("rejects a saved root that is no longer authorized", () => {
    const saved = parseSavedExplorerLocation(JSON.stringify({
      version: 3, provider: "google-drive", external_source_id: "source-1",
      assigned_root_id: "old-root", saved_at: 1_000, path: [{
        id: "old-root", name: "Old", kind: "folder", mime_type: "folder",
        provider: "google-drive", external_source_id: "source-1",
      }],
    }), "google-drive")!;
    expect(savedLocationIsAuthorized(saved, {
      sources: [{ external_source_id: "source-1", display_name: "Drive", folders: [{ id: "new-root", name: "New", external_source_id: "source-1" }] }],
      auto_selected_source_id: "source-1", auto_selected_folder_id: "new-root",
    })).toBe(false);
  });
});


describe("saved explorer location clearing", () => {
  it("clears only the active provider location for explicit logout", () => {
    const values = new Map<string, string>();
    const localStorage = {
      getItem: (key: string) => values.get(key) || null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    };
    (globalThis as { window?: unknown }).window = { localStorage };
    const googleKey = "creative-asset-manager:explorer-location:google-drive";
    const sharepointKey = "creative-asset-manager:explorer-location:sharepoint";
    localStorage.setItem(googleKey, "google");
    localStorage.setItem(sharepointKey, "sharepoint");

    clearSavedExplorerLocation("google-drive");

    expect(localStorage.getItem(googleKey)).toBeNull();
    expect(localStorage.getItem(sharepointKey)).toBe("sharepoint");
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


describe("breadcrumb sidebar hydration", () => {
  it("loads complete folder lists for every expanded breadcrumb level", () => {
    expect(driveExplorerSource).toContain(
      "void refreshTreePath(nextPath, source, treeSourceId, requestSequence, controller.signal)",
    );
    expect(driveExplorerSource).toContain(
      "await Promise.all(nodes.map(async node =>",
    );
  });

  it("does not cache a reconstructed path as a complete folder listing", () => {
    expect(driveExplorerSource).not.toContain(
      "treeFolderCache.current.set(folderCacheKey(source, parent.id), merged)",
    );
  });
});

describe("rename mutation", () => {
  it("renames through the tenant-scoped Explorer endpoint and refreshes the folder", () => {
    expect(driveExplorerSource).toContain('async function renameItem(itemId: string, requestedName: string)');
    expect(driveExplorerSource).toContain('{ method: "PATCH" }');
    expect(driveExplorerSource).toContain('"&name=" + encodeURIComponent(name)');
    expect(driveExplorerSource).toContain("await refreshCurrentFolder()");
  });
});
