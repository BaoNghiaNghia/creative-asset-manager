import { useState, type CSSProperties } from "react";
import { AssetGrid } from "./components/AssetGrid";
import { DriveEmpty } from "./components/DriveEmpty";
import { SidebarIcon } from "./components/Icons";
import { MediaViewer } from "./components/MediaViewer";
import { Sidebar } from "./components/Sidebar";
import { useDriveExplorer } from "./hooks/useDriveExplorer";
import { useResizableSidebar } from "./hooks/useResizableSidebar";
import type { Asset } from "./types";

export default function App() {
  const explorer = useDriveExplorer();
  const sidebar = useResizableSidebar();
  const [previewItem, setPreviewItem] = useState<Asset | null>(null);
  const activeId = explorer.path.at(-1)?.id;
  const rootFolders = explorer.childrenByParent.root ?? [];

  return <main
    className={"shell " + (sidebar.collapsed ? "sidebar-collapsed" : "")}
    style={{ "--sidebar-width": sidebar.width + "px" } as CSSProperties}
  >
    <Sidebar
      auth={explorer.auth}
      tags={explorer.tags}
      path={explorer.path}
      activeId={activeId}
      rootFolders={rootFolders}
      childrenByParent={explorer.childrenByParent}
      expanded={explorer.expanded}
      loadingNodes={explorer.loadingTreeIds}
      onOpen={explorer.open}
      onToggle={explorer.toggleTree}
      onPrefetch={explorer.scheduleFolderPrefetch}
      onCancelPrefetch={explorer.cancelFolderPrefetch}
      onCollapse={sidebar.collapse}
      onResizeStart={sidebar.startResize}
    />

    {sidebar.collapsed && <button
      className="sidebar-restore"
      onClick={sidebar.restore}
      aria-label="Open sidebar"
      title="Open sidebar"
    >
      <SidebarIcon open={false} />
    </button>}

    <section>
      <header>
        <label>
          ⌕
          <input
            value={explorer.query}
            onChange={event => explorer.setQuery(event.target.value)}
            placeholder="Search this folder"
          />
        </label>
        {explorer.auth.authenticated ? <div className="account">
          {explorer.auth.user?.picture
            ? <img className="avatar" src={explorer.auth.user.picture} alt="" referrerPolicy="no-referrer" />
            : <div className="avatar">{explorer.auth.user?.name?.slice(0, 2) || "G"}</div>}
          <button onClick={explorer.logout}>Sign out</button>
        </div> : <button
          className="header-login"
          onClick={() => window.location.assign("/api/auth/google/login")}
        >
          Sign in with Google
        </button>}
      </header>

      <nav>
        <div>{explorer.path.map((folder, index) => <button
          key={folder.id}
          onClick={() => explorer.open(folder.id, explorer.path.slice(0, index))}
        >
          {folder.name}
        </button>)}</div>
        {explorer.auth.authenticated && <button className="upload">＋ Upload</button>}
      </nav>

      {explorer.auth.checking ? <div className="state">Checking Google connection…</div>
        : !explorer.auth.authenticated ? <DriveEmpty oauthError={explorer.oauthError} />
        : <>
          {explorer.error && <div className="error">{explorer.error}</div>}
          <div className="title">
            <span>
              <h1>{explorer.path.at(-1)?.name || "My Drive"}</h1>
              <small>{explorer.items.length} items</small>
            </span>
            <b>▦　☷</b>
          </div>

          {explorer.loading ? <div className="state">Loading assets…</div> : <AssetGrid
            items={explorer.visibleItems}
            path={explorer.path}
            selected={explorer.selected}
            onOpen={explorer.open}
            onToggle={explorer.toggleSelection}
            onPrefetch={explorer.scheduleFolderPrefetch}
            onCancelPrefetch={explorer.cancelFolderPrefetch}
            onPreview={setPreviewItem}
          />}

          {!explorer.loading && !explorer.visibleItems.length && <div className="state">No assets found</div>}
          {explorer.selected.size > 0 && <div className="bulk">
            <b>{explorer.selected.size} selected</b>
            {explorer.tags.map(tag => <button key={tag.id} onClick={() => explorer.applyTag(tag.id)}>
              <i style={{ background: tag.color }} />{tag.name}
            </button>)}
            <button onClick={explorer.clearSelection}>×</button>
          </div>}
        </>}
    </section>

    {previewItem && <MediaViewer item={previewItem} onClose={() => setPreviewItem(null)} />}
  </main>;
}
