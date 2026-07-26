import { useState, type CSSProperties } from "react";
import { AssetGrid } from "./components/AssetGrid";
import { AssetDetailsPanel } from "./components/AssetDetailsPanel";
import { AnalyzeMetadataDialog } from "./components/AnalyzeMetadataDialog";
import { SearchGuide, SearchV2Controls } from "./components/SearchV2Controls";
import { DriveEmpty } from "./components/DriveEmpty";
import { EmptyAssets } from "./components/EmptyAssets";
import { SidebarIcon } from "./components/Icons";
import { MediaViewer } from "./components/MediaViewer";
import { Sidebar } from "./components/Sidebar";
import { useDriveExplorer } from "./hooks/useDriveExplorer";
import { useResizableSidebar } from "./hooks/useResizableSidebar";
import type { Asset } from "./types";

const visibilityFilters = ["all", "public", "draft"] as const;

export function isEligibleAnalysisItem(item: Asset): boolean {
  return item.kind === "image" && Boolean(item.internal_asset_id?.trim());
}

export function pruneSelectionToVisible(selected: ReadonlySet<string>, visibleItems: Asset[]): Set<string> {
  const visibleIds = new Set(visibleItems.map(item => item.id));
  const next = new Set([...selected].filter(id => visibleIds.has(id)));
  return next.size === selected.size ? selected as Set<string> : next;
}

export function getAnalysisSelectionState(selected: ReadonlySet<string>, visibleItems: Asset[]) {
  const selectedItems = visibleItems.filter(item => selected.has(item.id));
  const stale = selectedItems.length !== selected.size;
  const complete = selected.size > 0 && !stale && selectedItems.every(isEligibleAnalysisItem);
  const assetIds = complete ? selectedItems.map(item => item.internal_asset_id!) : [];
  const tooltip = selected.size === 0
    ? "Select imported image files to analyze."
    : stale
      ? "Selection changed. Reselect the visible images."
      : selectedItems.some(item => item.kind !== "image")
        ? "Only imported image files can be analyzed."
        : selectedItems.some(item => !item.internal_asset_id?.trim())
          ? "Selected images are still being imported. Refresh the folder and try again."
          : "Analyze selected assets.";
  return { selectedItems, assetIds, complete, tooltip };
}

export default function App() {
  const explorer = useDriveExplorer();
  const sidebar = useResizableSidebar();
  const [previewItem, setPreviewItem] = useState<Asset | null>(null);
  const initialDetailsAssetId = new URLSearchParams(window.location.search).get("asset");
  const [detailsAssetId, setDetailsAssetId] = useState<string | null>(initialDetailsAssetId);
  const [detailsItem, setDetailsItem] = useState<Asset | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(() => Boolean(initialDetailsAssetId || new URLSearchParams(window.location.search).get("details")));
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  function openDetails(item: Asset) {
    setDetailsOpen(true);
    setDetailsItem(item);
    setDetailsAssetId(item.internal_asset_id || null);
    const params = new URLSearchParams(window.location.search);
    params.set("details", "1");
    if (item.internal_asset_id) params.set("asset", item.internal_asset_id); else params.delete("asset");
    window.history.replaceState({}, "", window.location.pathname + "?" + params);
  }
  function closeDetails() {
    setDetailsOpen(false); setDetailsItem(null); setDetailsAssetId(null);
    const params = new URLSearchParams(window.location.search); params.delete("asset"); params.delete("details");
    window.history.replaceState({}, "", window.location.pathname + (params.toString() ? "?" + params : ""));
  }
  function toggleDetails() {
    if (detailsOpen) { closeDetails(); return; }
    const selectedItem = explorer.visibleItems.find(item => explorer.selected.has(item.id)) || detailsItem;
    if (selectedItem) { openDetails(selectedItem); return; }
    setDetailsOpen(true);
    const params = new URLSearchParams(window.location.search); params.set("details", "1");
    window.history.replaceState({}, "", window.location.pathname + "?" + params);
  }
  const activeId = explorer.path.at(-1)?.id;
  const sourceRootId = explorer.provider === "sharepoint" ? "sharepoint-root" : "root";
  const rootFolders = explorer.childrenByParent[sourceRootId] ?? [];
  const sourceName = explorer.provider === "sharepoint" ? "SharePoint" : "Google Drive";
  const selectedItems = explorer.visibleItems.filter(item => explorer.selected.has(item.id));
  const analysisAssetIds = selectedItems.flatMap(item => (
    item.kind !== "folder" && item.internal_asset_id ? [item.internal_asset_id] : []
  ));
  const completeAnalysisSelection = analysisAssetIds.length === explorer.selected.size;
  const analysisTooltip = completeAnalysisSelection
    ? "Analyze selected assets."
    : selectedItems.length === 1 && selectedItems[0].kind === "folder"
      ? "Folders cannot be analyzed. Select image or video files."
      : selectedItems.length === 1 && !selectedItems[0].internal_asset_id
        ? "This file has not been imported into the asset library yet."
        : "Some selected files are not ready for AI analysis.";

  return <main
    className={["shell", sidebar.collapsed ? "sidebar-collapsed" : "", detailsOpen ? "details-open" : ""].filter(Boolean).join(" ")}
    style={{ "--sidebar-width": sidebar.width + "px" } as CSSProperties}
  >
    <Sidebar
      provider={explorer.provider}
      auth={explorer.auth}
      authByProvider={explorer.authByProvider}
      tags={explorer.tags}
      path={explorer.path}
      activeId={activeId}
      rootFolders={rootFolders}
      childrenByParent={explorer.childrenByParent}
      expanded={explorer.expanded}
      loadingNodes={explorer.loadingTreeIds}
      onSelectProvider={explorer.selectProvider}
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
        <div className="search-tools">
          <label
            className={[
              "search-box",
              explorer.searching ? "searching" : "",
            ].filter(Boolean).join(" ")}
          >
            <span aria-hidden="true">⌕</span>
            <input
              value={explorer.query}
              disabled={!explorer.auth.authenticated || !explorer.searchReady}
              onChange={event => explorer.setQuery(event.target.value)}
              onKeyDown={event => event.key === "Escape" && explorer.setQuery("")}
              placeholder={!explorer.auth.authenticated
                ? "Connect Google Drive or SharePoint to search"
                : "Search this folder and subfolders"}
              aria-label="Search folders and files in this folder and all subfolders"
            />
            {explorer.query && <button
              type="button"
              className="search-clear"
              onClick={() => explorer.setQuery("")}
              aria-label="Clear search"
              title="Clear search"
            >×</button>}
          </label>
          {explorer.searchV2.active && <SearchGuide capabilities={explorer.searchV2.capabilities} />}
        </div>
        {explorer.auth.authenticated ? <div className="account">
          {explorer.auth.user?.picture
            ? <img className="avatar" src={explorer.auth.user.picture} alt="" referrerPolicy="no-referrer" />
            : <div className="avatar">{explorer.auth.user?.name?.slice(0, 2) || (explorer.provider === "sharepoint" ? "S" : "G")}</div>}
          <button onClick={explorer.logout}>Sign out</button>
        </div> : <div className="header-sources" aria-label="Available cloud sources">
          <span className="google">G</span><b>Google Drive</b>
          <i />
          <span className="microsoft">S</span><b>SharePoint</b>
        </div>}
      </header>

      {explorer.metadataIndex.state === "failed" && <div className="indexing-error" role="alert">
        <strong>{sourceName} metadata indexing failed.</strong>
        <span>{explorer.metadataIndex.error || "Check the API terminal for the detailed traceback."}</span>
      </div>}

      {explorer.auth.authenticated && <nav>
        <div>{explorer.path.map((folder, index) => <button
          key={folder.id}
          onClick={() => explorer.open(folder.id, explorer.path.slice(0, index))}
        >
          {folder.name}
        </button>)}</div>
        <button className="upload">＋ Upload</button>
      </nav>}

      {explorer.auth.checking ? <div className="state">Checking Google connection…</div>
        : !explorer.auth.authenticated ? <DriveEmpty
          oauthError={explorer.oauthError}
          activeProvider={explorer.provider}
          authByProvider={explorer.authByProvider}
          onSelectProvider={explorer.selectProvider}
        />
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
                    : !explorer.visibilityFilterReady
                      ? "Loading asset labels…"
                      : explorer.visibilityFilter === "all"
                        ? `${explorer.items.length} items`
                        : `${explorer.visibleItems.length} items shown`}</small>
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
            <div className="title-actions">
              <div className="visibility-filter" role="group" aria-label="Filter assets by visibility">
                {visibilityFilters.map(filter => <button
                  key={filter}
                  type="button"
                  className={explorer.visibilityFilter === filter ? "active" : ""}
                  aria-pressed={explorer.visibilityFilter === filter}
                  onClick={() => explorer.setVisibilityFilter(filter)}
                >{filter}</button>)}
              </div>
              <div className="view-tools" role="group" aria-label="View options">
                <b aria-label="Layout options">▦　☷</b>
                <button
                  type="button"
                  className={"details-toggle " + (detailsOpen ? "active" : "")}
                  aria-label={detailsOpen ? "Hide file information" : "Show file information"}
                  aria-pressed={detailsOpen}
                  title={detailsOpen ? "Hide details" : "Show details"}
                  onClick={toggleDetails}
                >i</button>
              </div>
            </div>
          </div>

          {explorer.searchV2.active && <SearchV2Controls capabilities={explorer.searchV2.capabilities} facets={explorer.searchV2.facets} selected={explorer.searchV2.selectedFacets} parsed={explorer.searchV2.parsed} onToggle={explorer.searchV2.toggleFacet} />}

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
            metadataByItem={explorer.metadataByItem}
            onOpen={explorer.open}
            onToggle={explorer.toggleSelection}
            onPrefetch={explorer.scheduleFolderPrefetch}
            onCancelPrefetch={explorer.cancelFolderPrefetch}
            onPreview={setPreviewItem}
            onRate={explorer.rateAsset}
            onDetails={openDetails}
            onFocus={item => detailsOpen && openDetails(item)}
          />}

          {!explorer.loading && !explorer.searching && !explorer.visibilityFilterReady && !explorer.visibleItems.length &&
            <div className="state">Loading asset labels…</div>}

          {!explorer.loading && !explorer.searching && explorer.visibilityFilterReady && !explorer.visibleItems.length && <EmptyAssets
            query={explorer.query}
            path={explorer.path}
            visibilityFilter={explorer.visibilityFilter}
            onClearSearch={() => explorer.setQuery("")}
            onClearFilter={() => explorer.setVisibilityFilter("all")}
            onOpen={explorer.open}
          />}
          {explorer.selected.size > 0 && <div className="bulk">
            <b>{explorer.selected.size} selected</b>
            <button
              type="button"
              disabled={!completeAnalysisSelection}
              title={analysisTooltip}
              onClick={() => setAnalyzeOpen(true)}
            >Analyze metadata</button>
            <span className="bulk-divider" />
            <div className="bulk-group">
              <small>Visibility</small>
              {explorer.tags.map(tag => <button key={tag.id} onClick={() => explorer.applyTag(tag.id)}>
                <i style={{ background: tag.color }} />{tag.name}
              </button>)}
            </div>
            <span className="bulk-divider" />
            <div className="bulk-group bulk-rating">
              <small>Rating</small>
              {[1, 2, 3, 4, 5].map(rating => <button
                key={rating}
                onClick={() => explorer.applyRating(rating)}
                aria-label={`Set rating to ${rating} stars`}
                title={`Set rating to ${rating} stars`}
              >{rating}★</button>)}
            </div>
            <button className="bulk-close" onClick={explorer.clearSelection} aria-label="Clear selection">×</button>
          </div>}
        </>}
    </section>

    {previewItem && <MediaViewer item={previewItem} onClose={() => setPreviewItem(null)} />}
    {detailsOpen && explorer.auth.authenticated && <AssetDetailsPanel
      item={detailsItem}
      assetId={detailsAssetId}
      metadata={detailsItem ? explorer.metadataByItem[detailsItem.id] : undefined}
      onPreview={setPreviewItem}
      onClose={closeDetails}
    />}
    <AnalyzeMetadataDialog
      open={analyzeOpen}
      assetIds={analysisAssetIds}
      sourceProvider={explorer.provider}
      authorized={completeAnalysisSelection && analysisAssetIds.length > 0}
      onClose={() => setAnalyzeOpen(false)}
    />
  </main>;
}
