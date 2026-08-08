import { Fragment, useEffect, useState, type PointerEventHandler } from "react";
import { fetchAccessIdentity } from "../../features/access_management";
import type { Asset, AuthState, Provider, ProviderSessions, Tag, TreeCache } from "../types";
import { DriveTreeNode } from "./DriveTree";
import { DriveIcon, SharePointIcon, SidebarIcon } from "./Icons";

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

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity().then(identity => {
      if (!alive) return;
      setCanViewAiOperations(mayViewAiOperations(identity.permissions));
    }).catch(() => { if (alive) setCanViewAiOperations(false); });
    return () => { alive = false; };
  }, []);

  return <aside className="sidebar">
    <button className="sidebar-collapse" onClick={onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar">
      <SidebarIcon open />
    </button>
    <div className="brand">
      <b>C</b>
      <span><strong>Creative assets</strong><small>{auth.user?.email || "Google Drive · SharePoint"}</small></span>
    </div>
    <div className="workspace-navigation" aria-label="Workspace navigation">
      <a href="/" aria-current="page">▧ Asset Explorer</a>
      {canViewAiOperations && <a href="/ai-operations">◉ AI Operations</a>}
      <a href="/settings/access">⚿ Access Management</a>
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
          {active && session.authenticated && source.provider === "google-drive" && applicationAuthenticated && <button className="source-reconnect" onClick={() => window.location.assign("/api/auth/google/connect-drive")}>Switch Google account</button>}
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
    <p>TAGS</p>
    {tags.map(tag => <button className="tag" key={tag.id}><i style={{ background: tag.color }} />{tag.name}</button>)}
    {auth.authenticated && <div className="connected-user"><span className="status-dot" /> Connected to {provider === "sharepoint" ? "SharePoint" : "Google Drive"}</div>}
    <div className="sidebar-resizer" onPointerDown={onResizeStart} role="separator" aria-label="Resize sidebar" aria-orientation="vertical" />
  </aside>;
}
