import { useEffect, useRef, useState, type CSSProperties, type DragEvent, type KeyboardEvent } from "react";
import { AssetGrid, AssetGridSkeleton } from "./components/AssetGrid";
import { AssetDetailsPanel } from "./components/AssetDetailsPanel";
import { AnalyzeMetadataDialog } from "./components/AnalyzeMetadataDialog";
import { SearchGuide, SearchControls } from "./components/SearchControls";
import { DriveEmpty } from "./components/DriveEmpty";
import { EmptyAssets } from "./components/EmptyAssets";
import { AmazonLogo, amazonAsin, EtsyLogo, etsyListingId, SidebarIcon, sourceFolderBrand } from "./components/Icons";
import { MediaViewer } from "./components/MediaViewer";
import { Sidebar } from "./components/Sidebar";
import { useDriveExplorer } from "./hooks/useDriveExplorer";
import { useResizableSidebar } from "./hooks/useResizableSidebar";
import type { Asset, SearchSuggestion } from "./types";

const visibilityFilters = ["all", "public", "draft"] as const;

type ExplorerClipboard = {
  items: Asset[];
  operation: "copy" | "cut";
};

type ShortcutNotice = {
  tone: "copy" | "cut" | "success" | "error";
  message: string;
};

function LoadMoreSentinel({
  enabled,
  loading,
  onLoadMore,
  root,
  resetKey,
  loadingLabel = "Loading more results…",
  readyLabel = "Scroll to load more",
}: {
  enabled: boolean;
  loading: boolean;
  onLoadMore: () => void;
  root: Element | null;
  resetKey?: string;
  loadingLabel?: string;
  readyLabel?: string;
}) {
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !enabled || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) onLoadMore();
    }, { root, rootMargin: "480px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [enabled, onLoadMore, root, resetKey]);

  if (!enabled && !loading) return null;
  return <div
    ref={sentinelRef}
    className="search-load-more"
    aria-live="polite"
    aria-busy={loading}
  >{loading ? loadingLabel : readyLabel}</div>;
}

export function isEligibleAnalysisItem(item: Asset): boolean {
  return item.kind === "image" && Boolean(item.internal_asset_id?.trim());
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

export function formatSearchDuration(durationMs: number | null): string | null {
  if (!Number.isFinite(durationMs) || durationMs === null || durationMs < 0) return null;
  return durationMs < 1_000 ? durationMs + " ms" : (durationMs / 1_000).toFixed(2) + " s";
}

export function getSearchSuggestionKeyAction(
  key: string,
  suggestionCount: number,
  suggestionIndex: number,
): "clear" | "next" | "previous" | "select" | "submit" | null {
  if (key === "Escape") return "clear";
  if (key === "ArrowDown" && suggestionCount > 0) return "next";
  if (key === "ArrowUp" && suggestionCount > 0) return "previous";
  if (key === "Enter") {
    return suggestionIndex >= 0 && suggestionIndex < suggestionCount ? "select" : "submit";
  }
  return null;
}

export const SEARCH_SUGGESTION_DISPLAY_MAX_LENGTH = 160;

/** Trust backend relevance while enforcing only display-safety constraints. */
export function curateSearchSuggestions(_query: string, values: SearchSuggestion[]): SearchSuggestion[] {
  const seen = new Set<string>();
  const result: SearchSuggestion[] = [];
  for (const value of values) {
    const text = value.text.trim().slice(0, SEARCH_SUGGESTION_DISPLAY_MAX_LENGTH);
    const key = text.toLocaleLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    result.push({ ...value, text, prefix: text, completion: "" });
    if (result.length === 10) break;
  }
  return result;
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
  const [suggestionIndex, setSuggestionIndex] = useState(-1);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const [confirm, setConfirm] = useState<{ message: string; run: () => void } | null>(null);
  const [clipboard, setClipboard] = useState<ExplorerClipboard | null>(null);
  const [shortcutNotice, setShortcutNotice] = useState<ShortcutNotice | null>(null);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const dragDepthRef = useRef(0);
  const searchBoxRef = useRef<HTMLDivElement | null>(null);
  const resultContainerRef = useRef<HTMLElement | null>(null);
  const autoAppendAttemptsRef = useRef(0);
  const autoAppendKeyRef = useRef("");
  const suggestions = curateSearchSuggestions(explorer.query, explorer.searchV3.suggestions);
  const showSuggestions = !suggestionsDismissed
    && explorer.searchV3.active
    && explorer.query.trim().length >= 2
    && (explorer.searchV3.suggestionsLoading || suggestions.length > 0 || Boolean(explorer.searchV3.suggestionsError));
  useEffect(() => {
    if (!showSuggestions) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!searchBoxRef.current?.contains(event.target as Node)) { setSuggestionIndex(-1); setSuggestionsDismissed(true); }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [showSuggestions]);
  function applySuggestion(value: string) {
    setSuggestionIndex(-1);
    setSuggestionsDismissed(true);
    explorer.setQuery(value);
  }
  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    const action = getSearchSuggestionKeyAction(event.key, suggestions.length, suggestionIndex);
    if (action === "clear") { setSuggestionIndex(-1); setSuggestionsDismissed(true); explorer.setQuery(""); return; }
    if (action === "next") { event.preventDefault(); setSuggestionIndex(current => (current + 1) % suggestions.length); return; }
    if (action === "previous") { event.preventDefault(); setSuggestionIndex(current => (current - 1 + suggestions.length) % suggestions.length); return; }
    if (action === "select") { event.preventDefault(); applySuggestion(suggestions[suggestionIndex].text); return; }
    if (action === "submit") { event.preventDefault(); setSuggestionIndex(-1); setSuggestionsDismissed(true); }
  }
  useEffect(() => {
    if (!shortcutNotice) return;
    const timer = window.setTimeout(() => setShortcutNotice(null), 4_000);
    return () => window.clearTimeout(timer);
  }, [shortcutNotice]);

  useEffect(() => {
    function targetAcceptsTextInput(target: EventTarget | null) {
      if (!(target instanceof HTMLElement)) return false;
      return target.matches("input, textarea, select, [contenteditable='true']") || Boolean(target.closest("[contenteditable='true']"));
    }
    function selectedVisibleItems() { return explorer.visibleItems.filter(item => explorer.selected.has(item.id)); }
    function storeClipboard(operation: ExplorerClipboard["operation"]) {
      const selectedItems = selectedVisibleItems();
      if (!selectedItems.length) return;
      setClipboard({ items: selectedItems, operation });
      const itemLabel = selectedItems.length + " item" + (selectedItems.length === 1 ? "" : "s");
      setShortcutNotice({ tone: operation, message: operation === "copy"
        ? "Copied " + itemLabel + ". Open a destination folder and press Ctrl+V."
        : "Cut " + itemLabel + ". Open a destination folder and press Ctrl+V to move." });
    }
    function handleExplorerShortcuts(event: globalThis.KeyboardEvent) {
      if (targetAcceptsTextInput(event.target) || confirm) return;
      const command = event.ctrlKey || event.metaKey;
      if (command && event.key.toLowerCase() === "c") {
        if (!selectedVisibleItems().length) return;
        event.preventDefault(); storeClipboard("copy"); return;
      }
      if (command && event.key.toLowerCase() === "x") {
        if (!selectedVisibleItems().length) return;
        event.preventDefault(); storeClipboard("cut"); return;
      }
      if (command && event.key.toLowerCase() === "v") {
        if (!clipboard?.items.length) return;
        event.preventDefault();
        if (explorer.provider !== "google-drive") {
          setShortcutNotice({ tone: "error", message: "Copy, cut and paste are currently available for Google Drive only." }); return;
        }
        const destination = explorer.currentFolderId;
        const count = clipboard.items.length;
        const action = clipboard.operation === "cut"
          ? Promise.all(clipboard.items.map(item => explorer.moveItem(item.id, destination)))
          : explorer.copyItems(clipboard.items.map(item => item.id), destination);
        void action.then(() => {
          explorer.clearSelection();
          if (clipboard.operation === "cut") setClipboard(null);
          const itemLabel = count + " item" + (count === 1 ? "" : "s");
          setShortcutNotice({ tone: "success", message: (clipboard.operation === "cut" ? "Moved " : "Copied ") + itemLabel + " to this folder." });
        }).catch(() => setShortcutNotice({
          tone: "error",
          message: clipboard.operation === "cut" ? "Could not move all cut items. Check that you can edit this folder." : "Could not paste the copied items. Check that you can edit this folder.",
        }));
        return;
      }
      if (event.key === "Delete") {
        const selectedItems = selectedVisibleItems();
        if (!selectedItems.length) return;
        event.preventDefault();
        setConfirm({
          message: "Delete " + selectedItems.length + " selected item" + (selectedItems.length === 1 ? "" : "s") + " from Google Drive?",
          run: () => {
            setConfirm(null);
            void Promise.all(selectedItems.map(item => explorer.deleteItem(item.id))).then(() => {
              explorer.clearSelection();
              setShortcutNotice({ tone: "success", message: "Deleted " + selectedItems.length + " item" + (selectedItems.length === 1 ? "" : "s") + "." });
            }).catch(() => setShortcutNotice({ tone: "error", message: "Could not delete all selected items. Check your Drive permissions." }));
          },
        });
      }
    }
    window.addEventListener("keydown", handleExplorerShortcuts);
    return () => window.removeEventListener("keydown", handleExplorerShortcuts);
  }, [clipboard, confirm, explorer]);

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
  const analysisSelection = getAnalysisSelectionState(explorer.selected, explorer.visibleItems);
  const analysisAssetIds = analysisSelection.assetIds;
  const completeAnalysisSelection = analysisSelection.complete;
  const analysisTooltip = analysisSelection.tooltip;
  const paginationResetKey = [explorer.query, explorer.provider, explorer.activeExternalSourceId || "", explorer.activeAssignedRootId || "", explorer.visibilityFilter, detailsOpen ? "details" : "no-details"].join("\u001f");
  useEffect(() => {
    if (autoAppendKeyRef.current !== paginationResetKey) {
      autoAppendKeyRef.current = paginationResetKey;
      autoAppendAttemptsRef.current = 0;
    }
  }, [paginationResetKey]);
  useEffect(() => {
    const container = resultContainerRef.current;
    if (!container || !explorer.searchV3.active || !explorer.searchV3.hasMore || explorer.searchV3.loading || explorer.searchV3.loadingMore) return;
    if (container.scrollHeight > container.clientHeight + 1 || autoAppendAttemptsRef.current >= 3) return;
    autoAppendAttemptsRef.current += 1;
    explorer.searchV3.loadMore();
  }, [explorer.searchV3.active, explorer.searchV3.hasMore, explorer.searchV3.loading, explorer.searchV3.loadingMore, explorer.searchV3.items.length, paginationResetKey, explorer.searchV3.loadMore]);
  const activeUploadCount = explorer.uploads.filter(upload => upload.status === "queued" || upload.status === "uploading").length;
  const failedUploadCount = explorer.uploads.filter(upload => upload.status === "failed").length;

  function dragContainsFiles(event: { dataTransfer: DataTransfer }) {
    return Array.from(event.dataTransfer.types).includes("Files");
  }
  function handleFileDragEnter(event: DragEvent<HTMLElement>) {
    if (!dragContainsFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setIsDraggingFiles(true);
  }
  function handleFileDragLeave(event: DragEvent<HTMLElement>) {
    if (!dragContainsFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFiles(false);
  }
  function handleFileDrop(event: DragEvent<HTMLElement>) {
    if (!dragContainsFiles(event)) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setIsDraggingFiles(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length && explorer.auth.authenticated) void explorer.uploadFiles(files);
  }

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
      onOpen={explorer.openFolder}
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

    <section
      ref={resultContainerRef}
      className={isDraggingFiles ? "explorer-content explorer-drop-active" : "explorer-content"}
      onDragEnter={handleFileDragEnter}
      onDragOver={event => { if (dragContainsFiles(event)) event.preventDefault(); }}
      onDragLeave={handleFileDragLeave}
      onDrop={handleFileDrop}
    >
      {isDraggingFiles && explorer.auth.authenticated && <div className="explorer-drop-overlay" role="status" aria-live="polite">
        <div><b>Drop files to upload</b><span>Files will be added to {explorer.path.at(-1)?.name || "My Drive"}.</span></div>
      </div>}
      <header>
        <div className="search-area">
          <div className="search-tools">
            <div
              ref={searchBoxRef}
              className={[
                "search-box",
                explorer.searching ? "searching" : "",
              ].filter(Boolean).join(" ")}
              aria-busy={explorer.searching}
            >
              <span aria-hidden="true">⌕</span>
              <input
                value={explorer.query}
                disabled={!explorer.auth.authenticated || explorer.auth.checking || !explorer.explorerReady}
                onChange={event => { setSuggestionIndex(-1); setSuggestionsDismissed(false); explorer.setQuery(event.target.value); }}
                onKeyDown={handleSearchKeyDown}
                placeholder={!explorer.auth.authenticated
                  ? "Connect Google Drive or SharePoint to search"
                  : "Search this folder and subfolders"}
                aria-label="Search folders and files in this folder and all subfolders"
                aria-autocomplete="list"
                aria-expanded={showSuggestions}
                aria-controls={showSuggestions ? "asset-search-suggestions" : undefined}
                aria-activedescendant={suggestionIndex >= 0 ? "asset-search-suggestion-" + suggestionIndex : undefined}
              />
              {explorer.query && <button
                type="button"
                className="search-clear"
                onClick={() => { setSuggestionIndex(-1); setSuggestionsDismissed(true); explorer.setQuery(""); }}
                aria-label="Clear search"
                title="Clear search"
              >{"\u00d7"}</button>}
              {showSuggestions && <div id="asset-search-suggestions" className="search-suggestions" role="listbox" aria-label="Search suggestions">
                <div className="search-suggestions-header"><strong>Suggestions</strong><span>Use ↑ ↓ then Enter</span></div>
                {explorer.searchV3.suggestionsError && <div className="search-suggestions-error" role="alert">
                  <span>{explorer.searchV3.suggestionsError}</span>
                  <button type="button" onClick={explorer.searchV3.retrySuggestions}>Retry suggestions</button>
                </div>}
                {explorer.searchV3.suggestionsLoading && !suggestions.length
                  ? <span className="search-suggestions-loading">Finding suggestions...</span>
                  : suggestions.map((suggestion, index) => <button
                    key={suggestion.kind + ":" + suggestion.text}
                    id={"asset-search-suggestion-" + index}
                    type="button"
                    role="option"
                    aria-selected={suggestionIndex === index}
                    className={suggestionIndex === index ? "active" : ""}
                    onMouseDown={event => event.preventDefault()}
                    onMouseEnter={() => setSuggestionIndex(index)}
                    onClick={() => applySuggestion(suggestion.text)}
                  ><span aria-hidden="true">{suggestion.kind === "filename" ? "F" : suggestion.kind === "visible_text" ? "T" : "S"}</span><span className="search-suggestion-text"><b>{suggestion.prefix}</b><em>{suggestion.completion}</em></span><small>{suggestion.kind === "filename" ? "File name" : suggestion.kind === "visible_text" ? "Detected text" : "Indexed text"}</small></button>)}
              </div>}
            </div>
            {explorer.searchV3.active && <SearchGuide capabilities={explorer.searchV3.capabilities} />}
          </div>
          {explorer.searching && <small className="search-waiting" role="status" aria-live="polite">
            Đang tìm kiếm…
          </small>}
          {explorer.query.trim() && explorer.searchDurationMs !== null && !explorer.searching && <small className="search-duration" role="status" aria-live="polite">
            {"T\u00ecm ki\u1ebfm ho\u00e0n t\u1ea5t trong "}{formatSearchDuration(explorer.searchDurationMs)}
          </small>}
        </div>
        {explorer.applicationAuthenticated ? <div className="account">
          {explorer.pureViewer && explorer.viewerSources.length > 1 && explorer.activeExternalSourceId && <label>
            <span className="sr-only">Assigned source</span>
            <select
              aria-label="Assigned Google Drive source"
              value={explorer.activeExternalSourceId || ""}
              onChange={event => void explorer.selectViewerSource(event.target.value)}
            >
              {explorer.viewerSources.map(source => <option key={source.external_source_id} value={source.external_source_id}>{source.display_name}</option>)}
            </select>
          </label>}
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

      {explorer.auth.authenticated && explorer.explorerReady && <nav>
        <div>{explorer.path.map((folder, index) => <button
          key={folder.id}
          onClick={() => explorer.open(folder.id, explorer.path.slice(0, index))}
        >
          {folder.name}
        </button>)}</div>
        <label className="upload" title="Upload files to the current folder">
          <span aria-hidden="true">＋</span> Upload
          <input
            hidden
            type="file"
            multiple
            onChange={event => {
              const files = Array.from(event.target.files || []);
              if (files.length) void explorer.uploadFiles(files);
              event.currentTarget.value = "";
            }}
          />
        </label>
      </nav>}

      {explorer.applicationAuthenticated === null ? <div className="state">Checking application session...</div>
        : explorer.applicationAuthenticated === false ? <DriveEmpty
          oauthError={explorer.oauthError}
          activeProvider={explorer.provider}
          authByProvider={explorer.authByProvider}
          onSelectProvider={explorer.selectProvider}
          applicationAuthenticated={explorer.applicationAuthenticated}
        />
        : !explorer.auth.authenticated ? <DriveEmpty
          oauthError={explorer.oauthError}
          activeProvider={explorer.provider}
          authByProvider={explorer.authByProvider}
          onSelectProvider={explorer.selectProvider}
          applicationAuthenticated
        />
        : explorer.pureViewer && explorer.viewerBootstrapState === "loading"
          ? <div className="state">Loading assigned Google Drive folders…</div>
        : explorer.pureViewer && explorer.viewerBootstrapState === "permission"
          ? <div className="state" role="alert"><strong>Folder access required</strong><p>{explorer.error || "No folders are assigned to this account."}</p></div>
        : explorer.pureViewer && explorer.viewerSources.length > 1 && !explorer.activeExternalSourceId
          ? <div className="state viewer-source-picker">
            <strong>Select an assigned source</strong>
            <p>Your account can access folders in more than one Google Drive source.</p>
            <div role="list" aria-label="Assigned Google Drive sources">
              {explorer.viewerSources.map(source => <button
                type="button"
                role="listitem"
                key={source.external_source_id}
                onClick={() => void explorer.selectViewerSource(source.external_source_id)}
              >{source.display_name}</button>)}
            </div>
          </div>
        : <>
          {explorer.error && <div className="error">{explorer.error}</div>}
          {shortcutNotice && <div className={`shortcut-toast shortcut-toast--${shortcutNotice.tone}`} role="status" aria-live="polite">
            <span className="shortcut-toast__icon" aria-hidden="true">{shortcutNotice.tone === "cut" ? "✂" : shortcutNotice.tone === "copy" ? "⧉" : shortcutNotice.tone === "success" ? "✓" : "!"}</span>
            <span>{shortcutNotice.message}</span><button type="button" onClick={() => setShortcutNotice(null)} aria-label="Dismiss shortcut notification">×</button>
          </div>}
          <div className="title">
            <span className="search-summary">
              <h1>
                {explorer.path.at(-1)?.name || "My Drive"}
                {(() => {
                  const currentName = explorer.path.at(-1)?.name || "";
                  const ancestorNames = explorer.path.slice(0, -1).map(folder => folder.name);
                  const asin = amazonAsin(currentName) || [...ancestorNames].reverse().map(amazonAsin).find(Boolean) || null;
                  const listingId = etsyListingId(currentName) || [...ancestorNames].reverse().map(etsyListingId).find(Boolean) || null;
                  const underEtsy = ancestorNames.some(name => sourceFolderBrand(name) === "etsy");
                  if (asin) return <a
                    className="amazon-redirect"
                    href={"https://www.amazon.com/dp/" + asin}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={"Open " + asin + " on Amazon"}
                    title={"Open " + asin + " on Amazon"}
                  ><AmazonLogo /><span className="amazon-external-mark" aria-hidden="true">⟶</span></a>;
                  return underEtsy && listingId ? <a
                    className="etsy-redirect"
                    href={"https://www.etsy.com/listing/" + listingId}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={"Open listing " + listingId + " on Etsy"}
                    title={"Open listing " + listingId + " on Etsy"}
                  ><EtsyLogo /><span className="etsy-external-mark" aria-hidden="true">⟶</span></a> : null;
                })()}
              </h1>
              <small>{explorer.searching
                ? "Searching with Search V3…"
                : explorer.searchComplete
                  ? `Completed · ${explorer.visibleItems.length} results`
                  : explorer.query
                    ? `${explorer.visibleItems.length} results in this folder and subfolders`
                    : !explorer.visibilityFilterReady
                      ? "Loading asset labels…"
                      : explorer.visibilityFilter === "all"
                        ? `${explorer.items.length} items`
                        : `${explorer.visibleItems.length} items shown`}</small>
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

          {explorer.searchV3.active && <SearchControls capabilities={explorer.searchV3.capabilities} facets={explorer.searchV3.facets} selected={explorer.searchV3.selectedFacets} parsed={explorer.searchV3.parsed} onToggle={explorer.searchV3.toggleFacet} />}

          {explorer.searchError && <div className="search-warning" role="alert">
            <span>{explorer.searchError} Showing the current folder contents.</span>
            <button type="button" onClick={explorer.retrySearch}>Retry Search V3</button>
          </div>}
          {explorer.loading || explorer.searching ? <AssetGridSkeleton /> : <AssetGrid
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

          {explorer.searchV3.active && <LoadMoreSentinel
            enabled={explorer.searchV3.hasMore}
            loading={explorer.searchV3.loadingMore}
            onLoadMore={explorer.searchV3.loadMore}
            root={resultContainerRef.current}
            resetKey={`${paginationResetKey}:${explorer.searchV3.items.length}`}
          />}
          {explorer.searchV3.active && explorer.searchV3.hasMore && <button type="button" className="search-load-more-button" onClick={explorer.searchV3.loadMore} disabled={explorer.searchV3.loadingMore}>Load more results</button>}
          {!explorer.query.trim() && !explorer.searchV3.active && <LoadMoreSentinel
            enabled={explorer.hasMoreFolderItems}
            loading={explorer.loadingMoreFolderItems}
            onLoadMore={explorer.loadMoreFolderItems}
            root={resultContainerRef.current}
            resetKey={`${explorer.provider}:${explorer.currentFolderId}:${explorer.items.length}`}
            loadingLabel="Loading more items…"
            readyLabel="Scroll to load more items"
          />}
          {!explorer.query.trim() && explorer.loadMoreFolderError && <div className="search-warning">
            Could not load more items. <button type="button" onClick={explorer.loadMoreFolderItems}>Retry</button>
          </div>}

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
            <button
              type="button"
              onClick={() => void explorer.refreshCurrentFolder()}
              disabled={explorer.loading}
              title="Refresh this folder to load newly imported assets"
            >Refresh assets</button>
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
      onOpenFolder={(folderId) => void explorer.openFolder(folderId)}
      onDelete={() => setConfirm({ message: "Delete this file from Google Drive?", run: () => { setConfirm(null); void explorer.deleteItem(detailsItem?.id || "").catch(reason => console.error(reason)); } })}
      onMove={() => { const destination = window.prompt("Enter destination folder ID"); if (destination && detailsItem) setConfirm({ message: "Move this file to the selected folder?", run: () => { setConfirm(null); void explorer.moveItem(detailsItem.id, destination).catch(() => undefined); } }); }}
    />}
    {explorer.uploads.length > 0 && <aside className="upload-panel" aria-label="Upload progress" aria-live="polite">
      <header>
        <div><b>{activeUploadCount ? `Uploading ${activeUploadCount} file${activeUploadCount === 1 ? "" : "s"}` : failedUploadCount ? "Uploads need attention" : "Uploads complete"}</b><small>{explorer.uploads.length} file{explorer.uploads.length === 1 ? "" : "s"} in this upload</small></div>
        <button onClick={() => explorer.clearUploads?.()} aria-label="Close upload progress">×</button>
      </header>
      {explorer.uploads.map(upload => <div className={"upload-row upload-" + upload.status} key={upload.id}>
        <span className="upload-file-icon" aria-hidden="true"></span>
        <span className="upload-file-name" title={upload.name}>{upload.name}</span>
        <span className="upload-status-icon" aria-label={upload.status === "failed" ? upload.error || "Upload failed." : upload.status === "completed" ? "Completed" : "Uploading..."}>{upload.status === "failed" ? "!" : ""}</span>
      </div>)}
    </aside>}
    {confirm && <div className="confirm-toast" role="alertdialog"><span>{confirm.message}</span><button onClick={confirm.run}>Confirm</button><button onClick={() => setConfirm(null)}>Cancel</button></div>}
    <AnalyzeMetadataDialog
      open={analyzeOpen}
      assetIds={analysisAssetIds}
      sourceProvider={explorer.provider}
      authorized={completeAnalysisSelection && analysisAssetIds.length > 0}
      onClose={() => setAnalyzeOpen(false)}
    />
  </main>;
}
