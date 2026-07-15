import type { PointerEventHandler } from "react";
import type { Asset, AuthState, Tag, TreeCache } from "../types";
import { DriveTreeNode } from "./DriveTree";
import { DriveIcon, SidebarIcon } from "./Icons";

type Props = {
  auth: AuthState;
  tags: Tag[];
  path: Asset[];
  activeId?: string;
  rootFolders: Asset[];
  childrenByParent: TreeCache;
  expanded: Set<string>;
  loadingNodes: Set<string>;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (node: Asset) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
  onCollapse: () => void;
  onResizeStart: PointerEventHandler<HTMLDivElement>;
};

export function Sidebar({
  auth,
  tags,
  path,
  activeId,
  rootFolders,
  childrenByParent,
  expanded,
  loadingNodes,
  onOpen,
  onToggle,
  onPrefetch,
  onCancelPrefetch,
  onCollapse,
  onResizeStart,
}: Props) {
  const rootAncestors = path.length > 0 && path[0].id === "root" ? [path[0]] : [];

  return <aside className="sidebar">
    <button className="sidebar-collapse" onClick={onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar">
      <SidebarIcon open />
    </button>
    <div className="brand">
      <b>C</b>
      <span><strong>Creative assets</strong><small>{auth.user?.email || "Google Drive"}</small></span>
    </div>
    <p>SOURCES</p>
    {auth.checking ? <div className="source-skeleton"><i /><i /><i /></div> : auth.authenticated ? <>
      <button className={"source " + (activeId === "root" ? "active" : "")} onClick={() => onOpen("root", [])}>
        <DriveIcon /><span>My Drive</span>
      </button>
      <div className="tree">
        {rootFolders.map(folder => <DriveTreeNode
          key={folder.id}
          node={folder}
          ancestors={rootAncestors}
          activeId={activeId}
          childrenByParent={childrenByParent}
          expanded={expanded}
          loadingNodes={loadingNodes}
          onOpen={onOpen}
          onToggle={onToggle}
          onPrefetch={onPrefetch}
          onCancelPrefetch={onCancelPrefetch}
        />)}
      </div>
    </> : <div className="connect-drive">
      <span className="google-mark">G</span>
      <strong>Connect Google Drive</strong>
      <small>Sign in to browse your folders and files.</small>
      <button onClick={() => window.location.assign("/api/auth/google/login")}>Sign in with Google</button>
    </div>}
    <p>TAGS</p>
    {tags.map(tag => <button className="tag" key={tag.id}><i style={{ background: tag.color }} />{tag.name}</button>)}
    {auth.authenticated && <div className="connected-user"><span className="status-dot" /> Connected to Google Drive</div>}
    <div className="sidebar-resizer" onPointerDown={onResizeStart} role="separator" aria-label="Resize sidebar" aria-orientation="vertical" />
  </aside>;
}
