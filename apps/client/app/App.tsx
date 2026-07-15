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
        <label
          className={[
            "search-box",
            explorer.searching ? "searching" : "",
            explorer.metadataIndex.state === "running" ? "indexing" : "",
            explorer.metadataIndex.state === "failed" ? "index-failed" : "",
          ].filter(Boolean).join(" ")}
          style={{ "--index-progress": explorer.metadataIndex.progress + "%" } as CSSProperties}
          title={explorer.metadataIndex.state === "completed"
            ? `${explorer.metadataIndex.indexed_count} Drive items indexed`
            : explorer.metadataIndex.status}
        >
          <span aria-hidden="true">⌕</span>
          <input
            value={explorer.query}
            disabled={!explorer.auth.authenticated || !explorer.searchReady}
            onChange={event => explorer.setQuery(event.target.value)}
            onKeyDown={event => event.key === "Escape" && explorer.setQuery("")}
            placeholder={!explorer.auth.authenticated
              ? "Sign in with Google to search"
              : explorer.metadataIndex.state === "running"
                ? explorer.metadataIndex.status
                : explorer.metadataIndex.state === "failed"
                  ? "Metadata indexing failed"
                  : "Search this folder and subfolders"}
            aria-label="Search folders and files in this folder and all subfolders"
          />
          {explorer.metadataIndex.state === "running" && <span className="index-percent-badge">
            {explorer.metadataIndex.progress}%
          </span>}
          {explorer.metadataIndex.state === "completed" && !explorer.query && <span className="index-ready-badge">
            ✓ Ready
          </span>}
          {explorer.metadataIndex.state === "failed" && <button
            type="button"
            className="index-retry"
            onClick={explorer.retryMetadataIndex}
          >
            Retry
          </button>}
          {explorer.query && <button
            type="button"
            className="search-clear"
            onClick={() => explorer.setQuery("")}
            aria-label="Clear search"
            title="Clear search"
          >×</button>}
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
            <span className="search-summary">
              <h1>{explorer.path.at(-1)?.name || "My Drive"}</h1>
              <small>{explorer.searching
                ? `${explorer.searchStatus} · ${explorer.searchProgress}%`
                : explorer.searchComplete
                  ? `Completed · 100% · ${explorer.visibleItems.length} results (${explorer.searchIndexedCount} indexed)`
                  : explorer.query
                    ? `${explorer.visibleItems.length} results in this folder and subfolders`
                    : `${explorer.items.length} items`}</small>
              {(explorer.searching || explorer.searchComplete) && <>
                <div
                  className={"search-progress " + (explorer.searchComplete ? "complete" : "")}
                  role="progressbar"
                  aria-label="Folder metadata indexing progress"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={explorer.searchProgress}
                >
                  <i style={{ width: explorer.searchProgress + "%" }} />
                </div>
                {explorer.searching && <em>
                  {explorer.searchProcessedFolders} folders processed
                  {explorer.searchPendingFolders > 0
                    ? ` · at least ${explorer.searchPendingFolders} remaining`
                    : ""}
                  {explorer.searchIndexedCount > 0
                    ? ` · ${explorer.searchIndexedCount} items indexed`
                    : ""}
                </em>}
              </>}
            </span>
            <b>▦　☷</b>
          </div>

          {explorer.searchError && <div className="search-warning">
            Subfolder search is temporarily unavailable. Showing matches from the current folder.
          </div>}
          {explorer.searchTruncated && <div className="search-warning">
            Search reached the metadata indexing limit; refine the query for more precise results.
          </div>}

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

          {!explorer.loading && !explorer.searching && !explorer.visibleItems.length && <div className="state">
            {explorer.query ? "No matching folders or files" : "No assets found"}
          </div>}
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
