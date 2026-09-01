import { Fragment, useEffect, useState, type PointerEventHandler, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { fetchAccessIdentity } from "../../features/access_management";
import type { Asset, AuthState, Provider, ProviderSessions, Tag, TreeCache } from "../types";
import { DriveTreeNode } from "./DriveTree";
import { BrandIcon, DriveIcon, SharePointIcon, SidebarIcon } from "./Icons";

type Props = {
  provider: Provider;
  auth: AuthState;
  authByProvider: ProviderSessions;
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

type WorkspaceNavIconName = "assets" | "operations" | "queue" | "access";

function WorkspaceNavIcon({ name }: { name: WorkspaceNavIconName }) {
  const paths: Record<WorkspaceNavIconName, ReactNode> = {
    assets: <><rect x="3" y="5" width="18" height="15" rx="2" /><path d="m4 17 5-5 3.5 3.5 2.5-2.5 5 5M8 9h.01" /></>,
    operations: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /><path d="M12 3v2M21 12h-2" /></>,
    queue: <><path d="M5 5h14M5 12h14M5 19h9" /><circle cx="4" cy="5" r=".7" fill="currentColor" stroke="none" /><circle cx="4" cy="12" r=".7" fill="currentColor" stroke="none" /><circle cx="4" cy="19" r=".7" fill="currentColor" stroke="none" /></>,
    access: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20c.6-3.5 3-5.5 7-5.5s6.4 2 7 5.5M18 4l2 2-2 2" /></>,
  };
  return <svg className="workspace-nav-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

const sources: Array<{ provider: Provider; label: string; login: string }> = [
  { provider: "google-drive", label: "Google Drive", login: "/api/auth/google/connect-drive" },
  { provider: "sharepoint", label: "SharePoint", login: "/api/auth/microsoft/login" },
];

export function Sidebar({
  provider, auth, authByProvider, tags, path, activeId, rootFolders,
  childrenByParent, expanded, loadingNodes, onSelectProvider, onOpen,
  onToggle, onPrefetch, onCancelPrefetch, onCollapse, onResizeStart,
  applicationAuthenticated = false,
}: Props) {
  const currentRoot = provider === "sharepoint" ? "sharepoint-root" : "root";
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
    <div className="workspace-navigation" aria-label="Workspace navigation">
      <a href="/" aria-current="page"><WorkspaceNavIcon name="assets" /><span>Asset Explorer</span></a>
      {canViewAiOperations && <a href="/ai-operations"><WorkspaceNavIcon name="operations" /><span>AI Operations</span></a>}
      {canViewAiOperations && <a href="/job-queue"><WorkspaceNavIcon name="queue" /><span>Job Queue</span></a>}
      <a href="/settings/access"><WorkspaceNavIcon name="access" /><span>Access Management</span></a>
    </div>
    <p>SOURCES</p>
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
          </button> : <button className="source provider-login" onClick={() => window.location.assign(
            applicationAuthenticated && source.provider === "google-drive"
              ? "/api/auth/google/connect-drive"
              : source.login
          )}>
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
            {rootFolders.map(folder => <DriveTreeNode
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
    {auth.authenticated && <div className="connected-user"><span className="status-dot" /> Connected to {provider === "sharepoint" ? "SharePoint" : "Google Drive"}</div>}
    <div className="sidebar-resizer" onPointerDown={onResizeStart} role="separator" aria-label="Resize sidebar" aria-orientation="vertical" />
  </aside>;
}
