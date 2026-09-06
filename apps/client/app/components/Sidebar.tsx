import { Fragment, useEffect, useState, type PointerEventHandler } from "react";
import { createPortal } from "react-dom";
import { fetchAccessIdentity } from "../../features/access_management";
import type { Asset, AuthState, ConnectedSource, Provider, ProviderSessions, Tag, TreeCache } from "../types";
import { DriveTreeNode, TreeChildrenSkeleton } from "./DriveTree";
import { BrandIcon, DriveIcon, SharePointIcon, SidebarIcon } from "./Icons";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
import googleDrivePlatformLogo from "../../assets/logos/google-drive-platform.png";
import oneDrivePlatformLogo from "../../assets/logos/onedrive-platform.png";

type Props = {
  provider: Provider;
  auth: AuthState;
  authByProvider: ProviderSessions;
  sources: ConnectedSource[];
  activeExternalSourceId: string | null;
  tags: Tag[];
  path: Asset[];
  activeId?: string;
  rootFolders: Asset[];
  childrenByParent: TreeCache;
  expanded: Set<string>;
  loadingNodes: Set<string>;
  onSelectProvider: (provider: Provider) => void;
  onSelectSource: (sourceId: string) => Promise<void>;
  onDisconnectSource: (sourceId: string) => Promise<void>;
  onSyncSource: (sourceId: string) => Promise<void>;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (node: Asset) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
  onCollapse: () => void;
  onResizeStart: PointerEventHandler<HTMLDivElement>;
  applicationAuthenticated?: boolean;
};

export function mayViewAiOperations(permissions: readonly string[]): boolean {
  return permissions.includes("ai_operations.read");
}

const sources: Array<{ provider: Provider; label: string; login: string }> = [
  { provider: "google-drive", label: "Google Drive", login: "/api/auth/google/connect-drive" },
  { provider: "onedrive", label: "OneDrive", login: "/api/auth/microsoft/connect-onedrive" },
  { provider: "sharepoint", label: "SharePoint", login: "/api/auth/microsoft/connect-sharepoint" },
];

function SourceIcon({ provider }: { provider: Provider }) {
  if (provider === "google-drive") return <img className="source-provider-logo" src={googleDrivePlatformLogo} alt="" aria-hidden="true" />;
  if (provider === "onedrive") return <img className="source-provider-logo" src={oneDrivePlatformLogo} alt="" aria-hidden="true" />;
  return <SharePointIcon />;
}

function sourceProvider(source: ConnectedSource): Provider {
  return source.source_type === "google_drive" ? "google-drive" : source.source_type;
}

type OneDriveAccountType = "personal" | "work";

function sourceLogin(provider: Provider, sourceId?: string, accountType?: OneDriveAccountType) {
  const route = provider === "google-drive" ? "/api/auth/google/connect-drive"
    : provider === "onedrive" ? "/api/auth/microsoft/connect-onedrive"
      : "/api/auth/microsoft/connect-sharepoint";
  const params = new URLSearchParams();
  if (sourceId) params.set("external_source_id", sourceId);
  if (provider === "onedrive" && accountType) params.set("account_type", accountType);
  return route + (params.size ? "?" + params.toString() : "");
}

function beginSourceOAuth(provider: Provider, sourceId?: string, accountType?: OneDriveAccountType): boolean {
  if (!window.camDesktop || provider === "sharepoint") return false;
  void window.camDesktop.beginOAuth({
    intent: provider === "google-drive" ? "google_drive_connect"
      : accountType === "personal" ? "onedrive_personal_connect" : "onedrive_work_connect",
    ...(sourceId ? { externalSourceId: sourceId } : {}),
  });
  return true;
}



export function Sidebar({
  provider, auth, authByProvider, sources: connectedSources, activeExternalSourceId, tags, path, activeId, rootFolders,
  childrenByParent, expanded, loadingNodes, onSelectProvider, onSelectSource, onDisconnectSource, onSyncSource, onOpen,
  onToggle, onPrefetch, onCancelPrefetch, onCollapse, onResizeStart,
  applicationAuthenticated = false,
}: Props) {
  const currentRoot = provider === "sharepoint" ? "sharepoint-root" : provider === "onedrive" ? "onedrive-root" : "root";
  const rootAncestors = path.length > 0 && path[0].id === currentRoot ? [path[0]] : [];
  const activePathIds = new Set(path.map(folder => folder.id));
  const [canViewAiOperations, setCanViewAiOperations] = useState(false);
  const [showSwitchGoogleConfirm, setShowSwitchGoogleConfirm] = useState(false);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity().then(identity => {
      if (!alive) return;
      setCanViewAiOperations(mayViewAiOperations(identity.permissions));
    }).catch(() => { if (alive) setCanViewAiOperations(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!showSwitchGoogleConfirm) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowSwitchGoogleConfirm(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [showSwitchGoogleConfirm]);

  return <aside className="sidebar">
    <button className="sidebar-collapse" onClick={onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar">
      <SidebarIcon open />
    </button>
    <div className="brand">
      <b><BrandIcon /></b>
      <span><strong>Creative assets</strong><small>{auth.user?.email || "Google Drive · SharePoint"}</small></span>
    </div>
    <WorkspaceNavigation active="assets" showOperations={canViewAiOperations} />
    <p>SOURCES</p>
    {Object.values(authByProvider).some(session => session.checking)
      ? <div className="source-skeleton"><i /><i /><i /></div>
      : sources.map(source => {
        const providerSources = connectedSources.filter(item => sourceProvider(item) === source.provider);
        const active = provider === source.provider;
        return <Fragment key={source.provider}>
          {providerSources.length ? providerSources.map(connected => {
            const selected = connected.status === "active" && activeExternalSourceId === connected.id;
            const account = connected.account.email || connected.display_name || "Connected account";
            const reconnectRequired = connected.status === "reconnect_required";
            return <div className="source-entry" key={connected.id}>
              <button className={"source " + (selected ? "active" : "")} onClick={() => {
                if (connected.status === "active") void onSelectSource(connected.id);
              }}>
                <SourceIcon provider={source.provider} />
                <span className="source-label"><strong>{source.label}</strong><small>({account})</small></span>
                {connected.status === "active" && <i className="source-connected" title="Connected" />}
                {reconnectRequired && <i className="source-attention" title="Reconnect required" />}
              </button>
              <div className="source-entry-actions" aria-label={source.label + " account actions"}>
                {connected.status === "active" && <button type="button" onClick={() => void onSelectSource(connected.id)}>Open</button>}
                {connected.status === "active" && connected.capabilities.sync && <button type="button" disabled={busySourceId === connected.id} onClick={() => {
                  setBusySourceId(connected.id);
                  void onSyncSource(connected.id).catch(() => undefined).finally(() => setBusySourceId(null));
                }}>{busySourceId === connected.id ? "Syncing..." : "Sync"}</button>}
                {connected.capabilities.reconnect && connected.status !== "disconnected" && <button type="button" onClick={() => {
                  const accountType = connected.metadata.drive_type === "personal" ? "personal" : "work";
                  if (!beginSourceOAuth(source.provider, connected.id, accountType)) window.location.assign(sourceLogin(source.provider, connected.id, accountType));
                }}>{reconnectRequired ? "Reconnect" : "Reauthorize"}</button>}
                {connected.capabilities.disconnect && connected.status !== "disconnected" && <button type="button" className="danger" disabled={busySourceId === connected.id} onClick={() => {
                  if (!window.confirm("Disconnect " + source.label + " account " + account + "?")) return;
                  setBusySourceId(connected.id);
                  void onDisconnectSource(connected.id).catch(() => undefined).finally(() => setBusySourceId(null));
                }}>{busySourceId === connected.id ? "Disconnecting..." : "Disconnect"}</button>}
                {connected.status === "disconnected" && <small className="source-disconnected">Disconnected</small>}
              </div>
              {selected && active && <div className="tree">
                {loadingNodes.has(currentRoot) && rootFolders.length === 0
                  ? <TreeChildrenSkeleton rows={5} />
                  : rootFolders.map(folder => <DriveTreeNode
                  key={folder.id} node={folder} ancestors={rootAncestors} activeId={activeId}
                  activePathIds={activePathIds} childrenByParent={childrenByParent}
                  expanded={expanded} loadingNodes={loadingNodes} onOpen={onOpen}
                  onToggle={onToggle} onPrefetch={onPrefetch} onCancelPrefetch={onCancelPrefetch}
                />)}
              </div>}
            </div>;
          }) : <button className="source provider-login" onClick={() => window.location.assign(
            applicationAuthenticated && source.provider === "google-drive"
              ? "/api/auth/google/connect-drive"
              : source.login
          )}>
            <SourceIcon provider={source.provider} />
            <span>Connect {source.label}</span><small>Sign in</small>
          </button>}
          {source.provider === "onedrive" && applicationAuthenticated && <button className="source-add-account" type="button" onClick={() => {
            if (!beginSourceOAuth("onedrive", undefined, "personal")) window.location.assign(sourceLogin("onedrive", undefined, "personal"));
          }}>+ Add personal OneDrive</button>}
          {source.provider === "onedrive" && applicationAuthenticated && providerSources.length > 0 && <button className="source-add-account" type="button" onClick={() => {
            if (!beginSourceOAuth("onedrive", undefined, "work")) window.location.assign(sourceLogin("onedrive", undefined, "work"));
          }}>+ Add work/school OneDrive</button>}
          {active && authByProvider[source.provider].authenticated && source.provider === "google-drive" && applicationAuthenticated && <button
            className="source-reconnect"
            type="button"
            onClick={() => setShowSwitchGoogleConfirm(true)}
            aria-label="Switch Google account"
          >
            <span className="source-reconnect-icon"><SourceIcon provider="google-drive" /></span>
            <span className="source-reconnect-copy">
              <strong>Switch Google account</strong>
              <small>Connect a different Drive account</small>
            </span>
            <svg className="source-reconnect-arrow" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M7 7h10l-2.5-2.5M17 7l-2.5 2.5M17 17H7l2.5 2.5M7 17l2.5-2.5" />
            </svg>
          </button>}
        </Fragment>;
      })}
    {showSwitchGoogleConfirm && createPortal(<div
      className="source-switch-dialog-backdrop"
      onMouseDown={event => event.target === event.currentTarget && setShowSwitchGoogleConfirm(false)}
    >
      <section className="source-switch-dialog" role="alertdialog" aria-modal="true" aria-labelledby="switch-google-title" aria-describedby="switch-google-description">
        <span className="source-switch-dialog-icon"><DriveIcon /></span>
        <div className="source-switch-dialog-copy">
          <span className="source-switch-dialog-kicker">GOOGLE DRIVE</span>
          <h2 id="switch-google-title">Switch Google account?</h2>
          <p id="switch-google-description">You will briefly leave Creative Asset Manager to choose another Google account. Your current connection stays unchanged until the new connection succeeds.</p>
        </div>
        <div className="source-switch-dialog-actions">
          <button type="button" className="secondary" onClick={() => setShowSwitchGoogleConfirm(false)}>Cancel</button>
          <button type="button" className="primary" autoFocus onClick={() => window.location.assign("/api/auth/google/connect-drive")}>Continue with Google</button>
        </div>
      </section>
    </div>, document.body)}
    <p>TAGS</p>
    {tags.map(tag => <button className="tag" key={tag.id}><i style={{ background: tag.color }} />{tag.name}</button>)}
    {auth.authenticated && <div className="connected-user"><span className="status-dot" /> Connected to {provider === "onedrive" ? "OneDrive" : provider === "sharepoint" ? "SharePoint" : "Google Drive"}</div>}
    <div className="sidebar-resizer" onPointerDown={onResizeStart} role="separator" aria-label="Resize sidebar" aria-orientation="vertical" />
  </aside>;
}
