import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ConnectedSource, ProviderSessions } from "../types";
import { Sidebar } from "./Sidebar";

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
});
