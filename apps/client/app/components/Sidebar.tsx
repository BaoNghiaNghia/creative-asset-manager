import { Fragment, useEffect, useState, type PointerEventHandler } from "react";
import { createPortal } from "react-dom";
import { fetchAccessIdentity } from "../../features/access_management";
import type { Asset, AuthState, ConnectedSource, Provider, ProviderSessions, Tag, TreeCache } from "../types";
import { DriveTreeNode, TreeChildrenSkeleton } from "./DriveTree";
import { BrandIcon, DriveIcon, SharePointIcon, SidebarIcon } from "./Icons";
import { WorkspaceNavigation } from "./WorkspaceNavigation";

type Props = {
  provider: Provider;
  auth: AuthState;
  authByProvider: ProviderSessions;
  cloudSources?: ConnectedSource[];
  activeExternalSourceId?: string | null;
  onSelectSource?: (sourceId: string) => void;
  onDisconnectSource?: (sourceId: string) => void;
  tags: Tag[];
  path: Asset[];
  activeId?: string;
  rootFolders: Asset[];
  childrenByParent: TreeCache;
  expanded: Set<string>;
  loadingNodes: Set<string>;
  onSelectProvider: (provider: Provider) => void;
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

export function Sidebar({
  provider, auth, authByProvider, cloudSources = [], activeExternalSourceId, onSelectSource, onDisconnectSource, tags, path, activeId, rootFolders,
  childrenByParent, expanded, loadingNodes, onSelectProvider, onOpen,
  onToggle, onPrefetch, onCancelPrefetch, onCollapse, onResizeStart,
  applicationAuthenticated = false,
}: Props) {
  const currentRoot = provider === "sharepoint" ? "sharepoint-root" : provider === "onedrive" ? "onedrive-root" : "root";
  const rootAncestors = path.length > 0 && path[0].id === currentRoot ? [path[0]] : [];
  const activePathIds = new Set(path.map(folder => folder.id));
  const [canViewAiOperations, setCanViewAiOperations] = useState(false);
  const [showSwitchGoogleConfirm, setShowSwitchGoogleConfirm] = useState(false);

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
    {cloudSources.map(source => <div className={"source-managed " + (source.id === activeExternalSourceId ? "active" : "")} key={source.id}>
      <button className="source" disabled={source.status !== "active"} onClick={() => onSelectSource?.(source.id)}>
        {source.source_type === "sharepoint" ? <SharePointIcon /> : <DriveIcon />}
        <span><b>{source.display_name || (source.source_type === "onedrive" ? "OneDrive" : source.source_type === "sharepoint" ? "SharePoint" : "Google Drive")}</b><small>{source.account.email || (source.status === "active" ? "Connected" : source.status === "reconnect_required" ? "Reconnect required" : "Disconnected")}</small></span>
      </button>
      {applicationAuthenticated && source.status !== "disconnected" && <span className="source-actions">
        <button onClick={() => {
          if (window.camDesktop && source.source_type !== "sharepoint") {
            void window.camDesktop.beginOAuth({ intent: source.source_type === "google_drive" ? "google_drive_connect" : "onedrive_connect", externalSourceId: source.id });
            return;
          }
          window.location.assign(source.source_type === "google_drive" ? "/api/auth/google/connect-drive?external_source_id=" + encodeURIComponent(source.id) : "/api/auth/microsoft/" + (source.source_type === "onedrive" ? "connect-onedrive" : "connect-sharepoint") + "?external_source_id=" + encodeURIComponent(source.id));
        }}>Reconnect</button>
        <button onClick={() => onDisconnectSource?.(source.id)}>Disconnect</button>
      </span>}
    </div>)}
    {Object.values(authByProvider).some(session => session.checking)
      ? <div className="source-skeleton"><i /><i /><i /></div>
      : sources.map(source => {
        const session = authByProvider[source.provider];
        const active = provider === source.provider;
        const sourceState = active ? activeId === currentRoot ? "active" : "active-path" : "";
        return <Fragment key={source.provider}>
          {session.authenticated ? <button className={"source " + sourceState} onClick={() => onSelectProvider(source.provider)}>
            {source.provider === "sharepoint" ? <SharePointIcon /> : <DriveIcon />}
            <span>{source.label}</span>{active && <i className="source-connected" title="Connected" />}
          </button> : <button className="source provider-login" onClick={() => {
            if (!applicationAuthenticated && window.camDesktop) {
              void window.camDesktop.beginOAuth({
                provider: source.provider === "google-drive" ? "google" : "microsoft",
              });
              return;
            }
            window.location.assign(
              applicationAuthenticated && source.provider === "google-drive"
                ? "/api/auth/google/connect-drive"
                : !applicationAuthenticated && source.provider !== "google-drive"
                  ? "/api/auth/microsoft/login"
                  : source.login,
            );
          }}>
            {source.provider === "sharepoint" ? <SharePointIcon /> : <DriveIcon />}
            <span>Connect {source.label}</span><small>Sign in</small>
          </button>}
          {active && session.authenticated && source.provider === "google-drive" && applicationAuthenticated && <button
            className="source-reconnect"
            type="button"
            onClick={() => setShowSwitchGoogleConfirm(true)}
            aria-label="Switch Google account"
          >
            <span className="source-reconnect-icon"><DriveIcon /></span>
            <span className="source-reconnect-copy">
              <strong>Switch Google account</strong>
              <small>Connect a different Drive account</small>
            </span>
            <svg className="source-reconnect-arrow" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M7 7h10l-2.5-2.5M17 7l-2.5 2.5M17 17H7l2.5 2.5M7 17l2.5-2.5" />
            </svg>
          </button>}
          {active && session.authenticated && <div className="tree">
            {loadingNodes.has(currentRoot) && rootFolders.length === 0
              ? <TreeChildrenSkeleton rows={5} />
              : rootFolders.map(folder => <DriveTreeNode
              key={folder.id} node={folder} ancestors={rootAncestors} activeId={activeId}
              activePathIds={activePathIds} childrenByParent={childrenByParent}
              expanded={expanded} loadingNodes={loadingNodes} onOpen={onOpen}
              onToggle={onToggle} onPrefetch={onPrefetch} onCancelPrefetch={onCancelPrefetch}
            />)}
          </div>}
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
