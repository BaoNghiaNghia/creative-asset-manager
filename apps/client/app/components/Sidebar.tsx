import { Fragment, useEffect, useState, type PointerEventHandler } from "react";
import { createPortal } from "react-dom";
import { fetchAccessIdentity } from "../../features/access_management";
import type { Asset, AuthState, ConnectedSource, Provider, ProviderSessions, Tag, TreeCache } from "../types";
import { DriveTreeNode, TreeChildrenSkeleton } from "./DriveTree";
import { BrandIcon, DriveIcon, SharePointIcon, SidebarIcon } from "./Icons";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
import { managementApi, type Share } from "../public-review-management/api";
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
  canManageReviewLinks?: boolean;
  onCollapse: () => void;
  onResizeStart: PointerEventHandler<HTMLDivElement>;
  applicationAuthenticated?: boolean;
};

export function mayViewAiOperations(permissions: readonly string[]): boolean {
  return permissions.includes("ai_operations.read");
}

export function activeShareFolderIds(shares: readonly Share[]): Map<string, string> {
  const result = new Map<string, string>();
  for (const share of shares) {
    if (share.status !== "active" || share.revoked_at) continue;
    for (const scope of share.scopes) result.set(scope.external_source_id + ":" + scope.folder_external_id, share.id);
  }
  return result;
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

type SourceContextMenu = {
  account: string;
  connected: ConnectedSource;
  label: string;
  provider: Provider;
  reconnectRequired: boolean;
  x: number;
  y: number;
};

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
  onToggle, onPrefetch, onCancelPrefetch, canManageReviewLinks = false, onCollapse, onResizeStart,
  applicationAuthenticated = false,
}: Props) {
  const currentRoot = provider === "sharepoint" ? "sharepoint-root" : provider === "onedrive" ? "onedrive-root" : "root";
  const rootAncestors = path.length > 0 && path[0].id === currentRoot ? [path[0]] : [];
  const activePathIds = new Set(path.map(folder => folder.id));
  const [canViewAiOperations, setCanViewAiOperations] = useState(false);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);
  const [sourceContextMenu, setSourceContextMenu] = useState<SourceContextMenu | null>(null);
  const [sharedFolderIds, setSharedFolderIds] = useState<Map<string, string>>(() => new Map());
  const [copyingShareId, setCopyingShareId] = useState<string | null>(null);
  const [reviewLinkNotice, setReviewLinkNotice] = useState("");

  useEffect(() => {
    let alive = true;
    fetchAccessIdentity().then(identity => {
      if (!alive) return;
      setCanViewAiOperations(mayViewAiOperations(identity.permissions));
    }).catch(() => { if (alive) setCanViewAiOperations(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!canManageReviewLinks) {
      setSharedFolderIds(new Map());
      return;
    }
    let alive = true;
    managementApi.list().then(value => {
      if (alive) setSharedFolderIds(activeShareFolderIds(value.items));
    }).catch(() => {
      if (alive) setSharedFolderIds(new Map());
    });
    return () => { alive = false; };
  }, [canManageReviewLinks]);

  const copyReviewLink = async (shareId: string) => {
    if (!navigator.clipboard?.writeText) {
      setReviewLinkNotice("Clipboard access is unavailable. Use Share for review to rotate the link.");
      return;
    }
    if (!window.confirm("Create and copy a new review link? The current link and its public sessions will stop working.")) return;
    setCopyingShareId(shareId);
    setReviewLinkNotice("");
    try {
      const share = await managementApi.rotate(shareId);
      if (!share.share_url) throw new Error("Missing one-time review link.");
      await navigator.clipboard.writeText(share.share_url);
      setReviewLinkNotice("A new secure review link was copied. The old link is no longer valid.");
    } catch {
      setReviewLinkNotice("Unable to copy a new review link. No link was shown.");
    } finally {
      setCopyingShareId(null);
    }
  };

  useEffect(() => {
    if (!sourceContextMenu) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSourceContextMenu(null);
    };
    const closeOnWindowChange = () => setSourceContextMenu(null);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnWindowChange);
    window.addEventListener("scroll", closeOnWindowChange, true);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnWindowChange);
      window.removeEventListener("scroll", closeOnWindowChange, true);
    };
  }, [sourceContextMenu]);

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
              <button className={"source " + (selected ? "active" : "")} title={reconnectRequired ? "Reconnect Google Drive" : "Right-click for source actions"} onClick={() => {
                if (connected.status === "active") {
                  void onSelectSource(connected.id);
                  return;
                }
                if (reconnectRequired && connected.capabilities.reconnect) {
                  const accountType = connected.metadata.drive_type === "personal" ? "personal" : "work";
                  if (!beginSourceOAuth(source.provider, connected.id, accountType)) {
                    window.location.assign(sourceLogin(source.provider, connected.id, accountType));
                  }
                }
              }} onContextMenu={event => {
                event.preventDefault();
                setSourceContextMenu({
                  account,
                  connected,
                  label: source.label,
                  provider: source.provider,
                  reconnectRequired,
                  x: Math.min(event.clientX, window.innerWidth - 196),
                  y: Math.min(event.clientY, window.innerHeight - 172),
                });
              }}>
                <SourceIcon provider={source.provider} />
                <span className="source-label"><strong>{source.label}</strong><small>({account})</small></span>
                {connected.status === "active" && <i className="source-connected" title="Connected" />}
                {reconnectRequired && <i className="source-attention" title="Reconnect required" />}
              </button>
              {connected.status === "disconnected" && <small className="source-disconnected">Disconnected</small>}
              {selected && active && <div className="tree">
                {loadingNodes.has(currentRoot) && rootFolders.length === 0
                  ? <TreeChildrenSkeleton rows={5} />
                  : rootFolders.map(folder => <DriveTreeNode
                  key={folder.id} node={folder} ancestors={rootAncestors} activeId={activeId}
                  activePathIds={activePathIds} childrenByParent={childrenByParent}
                  expanded={expanded} loadingNodes={loadingNodes} onOpen={onOpen}
                  onToggle={onToggle} onPrefetch={onPrefetch} onCancelPrefetch={onCancelPrefetch}
                  onCopyReviewLink={sharedFolderIds.has(connected.id + ":" + folder.id) ? () => copyReviewLink(sharedFolderIds.get(connected.id + ":" + folder.id)!) : undefined}
                  reviewLinkCopying={copyingShareId === sharedFolderIds.get(connected.id + ":" + folder.id)}
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
        </Fragment>;
      })}
    {reviewLinkNotice && <p className="review-link-notice" role="status">{reviewLinkNotice}</p>}
    {sourceContextMenu && createPortal(<div className="source-context-menu-backdrop" onMouseDown={() => setSourceContextMenu(null)}>
      <div
        className="source-context-menu"
        role="menu"
        aria-label={sourceContextMenu.label + " account actions"}
        style={{ left: sourceContextMenu.x, top: sourceContextMenu.y }}
        onMouseDown={event => event.stopPropagation()}
      >
        {sourceContextMenu.connected.status === "active" && <button type="button" role="menuitem" onClick={() => {
          setSourceContextMenu(null);
          void onSelectSource(sourceContextMenu.connected.id);
        }}>Open</button>}
        {sourceContextMenu.connected.status === "active" && sourceContextMenu.connected.capabilities.sync && <button type="button" role="menuitem" disabled={busySourceId === sourceContextMenu.connected.id} onClick={() => {
          const sourceId = sourceContextMenu.connected.id;
          setSourceContextMenu(null);
          setBusySourceId(sourceId);
          void onSyncSource(sourceId).catch(() => undefined).finally(() => setBusySourceId(null));
        }}>{busySourceId === sourceContextMenu.connected.id ? "Syncing..." : "Sync"}</button>}
        {sourceContextMenu.connected.capabilities.reconnect && sourceContextMenu.connected.status !== "disconnected" && <button type="button" role="menuitem" onClick={() => {
          const { connected, provider } = sourceContextMenu;
          const accountType = connected.metadata.drive_type === "personal" ? "personal" : "work";
          setSourceContextMenu(null);
          if (!beginSourceOAuth(provider, connected.id, accountType)) window.location.assign(sourceLogin(provider, connected.id, accountType));
        }}>{sourceContextMenu.reconnectRequired ? (sourceContextMenu.provider === "google-drive" ? "Reconnect Google Drive" : "Reconnect") : sourceContextMenu.provider === "google-drive" ? "Switch Google account" : "Reauthorize"}</button>}
        {sourceContextMenu.connected.capabilities.disconnect && sourceContextMenu.connected.status !== "disconnected" && <button type="button" role="menuitem" className="danger" disabled={busySourceId === sourceContextMenu.connected.id} onClick={() => {
          const { account, connected, label } = sourceContextMenu;
          if (!window.confirm("Disconnect " + label + " account " + account + "?")) return;
          const sourceId = connected.id;
          setSourceContextMenu(null);
          setBusySourceId(sourceId);
          void onDisconnectSource(sourceId).catch(() => undefined).finally(() => setBusySourceId(null));
        }}>{busySourceId === sourceContextMenu.connected.id ? "Disconnecting..." : "Disconnect"}</button>}
      </div>
    </div>, document.body)}
    <p>TAGS</p>
    {tags.map(tag => <button className="tag" key={tag.id}><i style={{ background: tag.color }} />{tag.name}</button>)}
    {auth.authenticated && <div className="connected-user"><span className="status-dot" /> Connected to {provider === "onedrive" ? "OneDrive" : provider === "sharepoint" ? "SharePoint" : "Google Drive"}</div>}
    <div className="sidebar-resizer" onPointerDown={onResizeStart} role="separator" aria-label="Resize sidebar" aria-orientation="vertical" />
  </aside>;
}
