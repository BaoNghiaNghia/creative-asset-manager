import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ConnectedSource, ProviderSessions } from "../types";
import { activeShareFolderIds, Sidebar } from "./Sidebar";
import type { Share } from "../public-review-management/api";

const sessions: ProviderSessions = {
  "google-drive": { authenticated: false, user: null, checking: false },
  onedrive: { authenticated: true, user: null, checking: false },
  sharepoint: { authenticated: false, user: null, checking: false },
};

const source = (id: string, email: string): ConnectedSource => ({
  id, source_type: "onedrive", display_name: "OneDrive", status: "active",
  provider: "microsoft", connection_purpose: "onedrive_source",
  account: { provider_account_id: id + "-account", email },
  metadata: {}, capabilities: { browse: true, sync: true, write: false, reconnect: true, disconnect: true },
});

describe("Sidebar multi-source accounts", () => {
  it("renders each OneDrive account independently and keeps actions in its context menu", () => {
    const markup = renderToStaticMarkup(<Sidebar
      provider="onedrive" auth={sessions.onedrive} authByProvider={sessions}
      sources={[source("one", "one@example.com"), source("two", "two@example.com")]}
      activeExternalSourceId="two" tags={[]} path={[]} activeId={undefined}
      rootFolders={[]} childrenByParent={{}} expanded={new Set()} loadingNodes={new Set()}
      onSelectProvider={() => undefined} onSelectSource={async () => undefined}
      onDisconnectSource={async () => undefined} onSyncSource={async () => undefined}
      onOpen={() => undefined} onToggle={() => undefined} onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined} onCollapse={() => undefined} onResizeStart={() => undefined}
      applicationAuthenticated
    />);

    expect(markup).toContain("(one@example.com)");
    expect(markup).toContain("(two@example.com)");
    expect(markup).toContain("+ Add personal OneDrive");
    expect(markup).toContain("+ Add work/school OneDrive");
    expect(markup).toContain('title="Right-click for source actions"');
    expect(markup).not.toContain(">Open</button>");
    expect(markup).not.toContain(">Sync</button>");
    expect(markup).not.toContain(">Reauthorize</button>");
    expect(markup).not.toContain(">Disconnect</button>");
  });
  it("makes a permanently unavailable source directly reconnectable", () => {
    const reconnecting: ConnectedSource = { ...source("google-reconnect", "drive@example.com"), source_type: "google_drive", provider: "google", status: "reconnect_required" };
    const markup = renderToStaticMarkup(<Sidebar
      provider="google-drive" auth={{ authenticated: true, user: null, checking: false }} authByProvider={{ ...sessions, "google-drive": { authenticated: true, user: null, checking: false } }}
      sources={[reconnecting]} activeExternalSourceId={null} tags={[]} path={[]} activeId={undefined}
      rootFolders={[]} childrenByParent={{}} expanded={new Set()} loadingNodes={new Set()}
      onSelectProvider={() => undefined} onSelectSource={async () => undefined}
      onDisconnectSource={async () => undefined} onSyncSource={async () => undefined}
      onOpen={() => undefined} onToggle={() => undefined} onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined} onCollapse={() => undefined} onResizeStart={() => undefined}
      applicationAuthenticated
    />);

    expect(markup).toContain('title="Reconnect Google Drive"');
    expect(markup).toContain('title="Reconnect required"');
  });
  it("keeps Google account switching in the source context menu rather than rendering a sidebar card", () => {
    const google: ConnectedSource = {
      ...source("google-one", "drive@example.com"),
      source_type: "google_drive",
      display_name: "Google Drive",
      provider: "google",
    };
    const markup = renderToStaticMarkup(<Sidebar
      provider="google-drive" auth={{ authenticated: true, user: null, checking: false }} authByProvider={{ ...sessions, "google-drive": { authenticated: true, user: null, checking: false } }}
      sources={[google]}
      activeExternalSourceId="google-one" tags={[]} path={[]} activeId={undefined}
      rootFolders={[]} childrenByParent={{}} expanded={new Set()} loadingNodes={new Set()}
      onSelectProvider={() => undefined} onSelectSource={async () => undefined}
      onDisconnectSource={async () => undefined} onSyncSource={async () => undefined}
      onOpen={() => undefined} onToggle={() => undefined} onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined} onCollapse={() => undefined} onResizeStart={() => undefined}
      applicationAuthenticated
    />);

    expect(markup).toContain('title="Right-click for source actions"');
    expect(markup).not.toContain("Connect a different Drive account");
    expect(markup).not.toContain("source-reconnect");
  });


  it("identifies only active, non-revoked share scopes for source-tree link actions", () => {
    const folders = activeShareFolderIds([
      { id: "active-share", status: "active", revoked_at: null, scopes: [{ external_source_id: "google-one", folder_external_id: "folder-1" }] },
      { id: "revoked-share", status: "active", revoked_at: "2026-09-18T00:00:00Z", scopes: [{ external_source_id: "google-one", folder_external_id: "folder-2" }] },
      { id: "inactive-share", status: "revoked", revoked_at: null, scopes: [{ external_source_id: "google-one", folder_external_id: "folder-3" }] },
    ] as Share[]);

    expect(folders.get("google-one:folder-1")).toBe("active-share");
    expect(folders.has("google-one:folder-2")).toBe(false);
    expect(folders.has("google-one:folder-3")).toBe(false);
  });

});
