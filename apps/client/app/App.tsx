import { isPreviewableAsset } from "./utils/fileType";
import { useEffect, useRef, useState, type CSSProperties, type DragEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { AssetGrid, AssetGridSkeleton } from "./components/AssetGrid";
import { VideoSearchResults } from "./components/VideoSearchResults";
import { VideoSearchPlayer } from "./components/VideoSearchPlayer";
import type { VideoSearchItem } from "./hooks/useVideoSearch";
import { useVideoSearch } from "./hooks/useVideoSearch";
import { AssetContextMenu, type AssetContextMenuPosition } from "./components/AssetContextMenu";
import { AssetDetailsPanel } from "./components/AssetDetailsPanel";
import { AnalyzeMetadataDialog } from "./components/AnalyzeMetadataDialog";
import { SearchControls } from "./components/SearchControls";
import { DriveEmpty } from "./components/DriveEmpty";
import { EmptyAssets } from "./components/EmptyAssets";
import { AmazonLogo, amazonAsin, EtsyLogo, etsyListingId, SidebarIcon, sourceFolderBrand } from "./components/Icons";
import { MediaViewer } from "./components/MediaViewer";
import { FolderNoteDrawer } from "./components/FolderNoteDrawer";
import { Sidebar } from "./components/Sidebar";
import { useDriveExplorer } from "./hooks/useDriveExplorer";
import { useResizableSidebar } from "./hooks/useResizableSidebar";
import { explorerAssetUrl } from "./utils/mediaUrls";
import { folderNotePreview, productFolderKind } from "./utils/folderNotes";
import type { Asset, SearchSuggestion } from "./types";

const visibilityFilters = ["all", "public", "draft"] as const;
export const DEFAULT_SEARCH_MEDIA_MODE = "all" as const;
export type SearchMediaMode = typeof DEFAULT_SEARCH_MEDIA_MODE | "images" | "videos";

export function parseSearchMediaMode(value: string | null): SearchMediaMode {
  return value === "images" || value === "videos" || value === "all"
    ? value
    : DEFAULT_SEARCH_MEDIA_MODE;
}

export function searchIncludesImages(mode: SearchMediaMode): boolean {
  return mode === "all" || mode === "images";
}

export function searchIncludesVideos(mode: SearchMediaMode): boolean {
  return mode === "all" || mode === "videos";
}

type ExplorerClipboard = {
  items: Asset[];
  operation: "copy" | "cut";
};

type AssetContextState = {
  item: Asset;
  position: AssetContextMenuPosition;
};

type ShortcutNotice = {
  tone: "copy" | "cut" | "success" | "error";
  message: string;
};

export function accountAvatarLabel(name: string | undefined, provider: string): string {
  const initials = name?.trim().slice(0, 2).toUpperCase();
  return initials || (provider === "sharepoint" ? "S" : "G");
}

function AccountAvatar({
  picture,
  name,
  provider,
}: {
  picture: string | undefined;
  name: string | undefined;
  provider: string;
}) {
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [picture]);

  if (picture && !failed) {
    return <img
      className="avatar"
      src={picture}
      alt=""
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />;
  }

  return <div className="avatar" role="img" aria-label="User avatar">
    {accountAvatarLabel(name, provider)}
  </div>;
}

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
export function curateSearchSuggestions(query: string, values: SearchSuggestion[]): SearchSuggestion[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const seen = new Set<string>();
  const result: SearchSuggestion[] = [];
  for (const value of values) {
    const text = value.text.trim().slice(0, SEARCH_SUGGESTION_DISPLAY_MAX_LENGTH);
    const key = text.toLocaleLowerCase();
    if (!text || key === normalizedQuery || seen.has(key)) continue;
    seen.add(key);
    const prefix = value.prefix.trim();
    const completion = value.completion.trimEnd();
    result.push({
      ...value,
      text,
      prefix: prefix && text.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase()) ? prefix : text,
      completion: prefix && text.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase()) ? completion : "",
    });
    if (result.length === 10) break;
  }
  return result;
}

export default function App() {
  const [searchMediaMode, setSearchMediaMode] = useState<SearchMediaMode>(
    () => parseSearchMediaMode(new URLSearchParams(window.location.search).get("media")),
  );
  const [imageResultsExpanded, setImageResultsExpanded] = useState(true);
  const [videoResultsExpanded, setVideoResultsExpanded] = useState(true);
  const imageSearchEnabled = searchIncludesImages(searchMediaMode);
  const videoSearchEnabled = searchIncludesVideos(searchMediaMode);
  const explorer = useDriveExplorer(imageSearchEnabled);
  const videoSearch = useVideoSearch({
    authenticated: explorer.auth.authenticated,
    enabled: videoSearchEnabled,
    query: explorer.query,
    provider: explorer.provider,
    externalSourceId: explorer.activeExternalSourceId,
  });
  const searchBusy = explorer.query.trim().length > 0
    && ((imageSearchEnabled && explorer.searching) || (videoSearchEnabled && videoSearch.loading));
  const sidebar = useResizableSidebar();
  const [previewItem, setPreviewItem] = useState<Asset | null>(null);
  const [playbackItem, setPlaybackItem] = useState<VideoSearchItem | null>(null);
  const initialDetailsAssetId = new URLSearchParams(window.location.search).get("asset");
  const [detailsAssetId, setDetailsAssetId] = useState<string | null>(initialDetailsAssetId);
  const [detailsItem, setDetailsItem] = useState<Asset | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(() => Boolean(initialDetailsAssetId || new URLSearchParams(window.location.search).get("details")));
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  const [suggestionIndex, setSuggestionIndex] = useState(-1);
  const [suggestionsDismissed, setSuggestionsDismissed] = useState(false);
  const [confirm, setConfirm] = useState<{ message: string; run: () => void } | null>(null);
  const [clipboard, setClipboard] = useState<ExplorerClipboard | null>(null);
  const [assetContextMenu, setAssetContextMenu] = useState<AssetContextState | null>(null);
  const [shortcutNotice, setShortcutNotice] = useState<ShortcutNotice | null>(null);
  const [isDraggingFiles, setIsDraggingFiles] = useState(false);
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [folderNoteOpen, setFolderNoteOpen] = useState(false);
  const [folderNoteSummary, setFolderNoteSummary] = useState("");
  const [folderNoteAvailable, setFolderNoteAvailable] = useState(false);
  const dragDepthRef = useRef(0);
  const newMenuRef = useRef<HTMLDivElement | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const searchBoxRef = useRef<HTMLDivElement | null>(null);
  const resultContainerRef = useRef<HTMLElement | null>(null);
  const autoAppendAttemptsRef = useRef(0);
  const autoAppendKeyRef = useRef("");
  const suggestions = curateSearchSuggestions(explorer.query, explorer.searchV3.suggestions);
  const showSuggestions = imageSearchEnabled && !suggestionsDismissed
    && explorer.searchV3.active
    && explorer.query.trim().length >= 2
    && (explorer.searchV3.suggestionsLoading || suggestions.length > 0 || Boolean(explorer.searchV3.suggestionsError));
  useEffect(() => {
    const restoreMediaMode = () => {
      setSearchMediaMode(parseSearchMediaMode(new URLSearchParams(window.location.search).get("media")));
    };
    window.addEventListener("popstate", restoreMediaMode);
    return () => window.removeEventListener("popstate", restoreMediaMode);
  }, []);

  function selectSearchMediaMode(mode: SearchMediaMode) {
    setSearchMediaMode(mode);
    const params = new URLSearchParams(window.location.search);
    params.set("media", mode);
    window.history.replaceState({}, "", window.location.pathname + "?" + params.toString());
  }

  useEffect(() => {
    const folder = explorer.path.at(-1);
    if (!folder || folder.id === "root") { setFolderNoteSummary(""); setFolderNoteAvailable(false); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({ provider: explorer.provider });
    if (explorer.activeExternalSourceId) params.set("external_source_id", explorer.activeExternalSourceId);
    fetch("/api/explorer/folders/" + encodeURIComponent(folder.id) + "/note?" + params.toString(), { signal: controller.signal })
      .then(response => response.ok ? response.json() : null)
      .then(value => {
        setFolderNoteAvailable(Boolean(value?.note_owner_folder_id));
        setFolderNoteSummary(value?.content_markdown ? folderNotePreview(value.content_markdown) : "");
      })
      .catch(() => { if (!controller.signal.aborted) { setFolderNoteSummary(""); setFolderNoteAvailable(false); } });
    return () => controller.abort();
  }, [explorer.path, explorer.provider, explorer.activeExternalSourceId]);

  useEffect(() => {
    if (!confirm) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setConfirm(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [confirm]);
  useEffect(() => {
    if (!newMenuOpen) return;
    const closeMenu = (event: MouseEvent) => {
      if (newMenuRef.current && !newMenuRef.current.contains(event.target as Node)) setNewMenuOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setNewMenuOpen(false);
    };
    window.addEventListener("mousedown", closeMenu);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("mousedown", closeMenu);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [newMenuOpen]);

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


  function contextAncestors(item: Asset): Asset[] {
    if (!item.ancestor_ids?.length || item.ancestor_ids.length !== item.ancestor_names?.length) {
      return explorer.path;
    }
    return item.ancestor_ids.map((id, index) => ({
      provider: item.provider,
      id,
      name: item.ancestor_names?.[index] || "Folder",
      kind: "folder",
      mime_type: "application/vnd.google-apps.folder",
      external_source_id: item.external_source_id,
    }));
  }

  function openContextItem(item: Asset) {
    if (item.kind === "folder") {
      void explorer.open(item.id, contextAncestors(item));
      return;
    }
    if (isPreviewableAsset(item)) setPreviewItem(item);
    else openDetails(item);
  }

  function copyContextItem(item: Asset) {
    setClipboard({ items: [item], operation: "copy" });
    setShortcutNotice({ tone: "copy", message: "Copied 1 item. Open a destination folder and press Ctrl+V." });
  }

  function moveContextItem(item: Asset) {
    const destination = window.prompt("Enter destination folder ID");
    if (!destination) return;
    setConfirm({
      message: "Move this item to the selected folder?",
      run: () => {
        setConfirm(null);
        void explorer.moveItem(item.id, destination)
          .then(() => setShortcutNotice({ tone: "success", message: "Item moved successfully." }))
          .catch(() => setShortcutNotice({ tone: "error", message: "Could not move this item." }));
      },
    });
  }

  function deleteContextItem(item: Asset) {
    setConfirm({
      message: "Move this item to Google Drive trash?",
      run: () => {
        setConfirm(null);
        void explorer.deleteItem(item.id)
          .then(() => setShortcutNotice({ tone: "success", message: "Item moved to trash." }))
          .catch(() => setShortcutNotice({ tone: "error", message: "Could not move this item to trash." }));
      },
    });
  }

  const videoResults = <>
    {videoSearch.error && <div className="search-warning" role="alert"><span>{videoSearch.error}</span></div>}
    {videoSearch.loading
      ? <AssetGridSkeleton />
      : videoSearch.items.length
        ? <VideoSearchResults items={videoSearch.items} onOpen={setPlaybackItem} />
        : <div className="state">No videos matched this search.</div>}
  </>;

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
      applicationAuthenticated={explorer.applicationAuthenticated === true}
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
                searchBusy ? "searching" : "",
              ].filter(Boolean).join(" ")}
              aria-busy={searchBusy}
            >
              <span aria-hidden="true">⌕</span>
              <input
                value={explorer.query}
                disabled={!explorer.auth.authenticated || explorer.auth.checking || !explorer.explorerReady}
                onChange={event => { setSuggestionIndex(-1); setSuggestionsDismissed(false); explorer.setQuery(event.target.value); }}
                onKeyDown={handleSearchKeyDown}
                placeholder={!explorer.auth.authenticated
                  ? "Connect Google Drive or SharePoint to search"
                  : searchMediaMode === "all" ? "Search images & videos" : searchMediaMode === "videos" ? "Search videos" : "Search images"}
                aria-label="Search images and videos"
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
          </div>
          {searchBusy && <small className="search-waiting" role="status" aria-live="polite">
            {searchMediaMode === "all"
              ? explorer.searching && videoSearch.loading
                ? "Searching images & videos..."
                : explorer.searching
                  ? "Searching images..."
                  : "Searching videos..."
              : "Searching..."}
          </small>}
          {imageSearchEnabled && explorer.query.trim() && explorer.searchDurationMs !== null && !explorer.searching && <small className="search-duration" role="status" aria-live="polite">
            {"T\u00ecm ki\u1ebfm ho\u00e0n t\u1ea5t trong "}{formatSearchDuration(explorer.searchDurationMs)}
          </small>}
        </div>
        <div className="search-mode-tabs" role="radiogroup" aria-label="Search media type">
          {(["all", "images", "videos"] as SearchMediaMode[]).map(mode => <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={searchMediaMode === mode}
            className={searchMediaMode === mode ? "active" : ""}
            onClick={() => selectSearchMediaMode(mode)}
          >{mode === "all" ? "All" : mode === "images" ? "Images" : "Videos"}</button>)}
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
          <AccountAvatar
            picture={explorer.auth.user?.picture}
            name={explorer.auth.user?.name}
            provider={explorer.provider}
          />
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
        <div className="explorer-create-actions" ref={newMenuRef}>
          <button type="button" className="explorer-new-trigger" aria-haspopup="menu" aria-expanded={newMenuOpen} aria-controls="explorer-new-menu" onClick={() => setNewMenuOpen(open => !open)}><span aria-hidden="true">+</span>New<span className="explorer-new-caret" aria-hidden="true">v</span></button>
          {newMenuOpen && <div id="explorer-new-menu" className="explorer-new-menu" role="menu" aria-label="Create or upload">
            {!explorer.pureViewer && <>
              <button type="button" role="menuitem" onClick={() => { setNewMenuOpen(false); const name = window.prompt("Folder name"); if (name?.trim()) void explorer.createFolder(name.trim()).catch(() => window.alert("Unable to create folder.")); }}><span className="explorer-new-icon folder" aria-hidden="true">[]</span><span><b>New folder</b><small>Create in this folder</small></span></button>
              <button type="button" role="menuitem" onClick={() => { setNewMenuOpen(false); const name = window.prompt("Text file name"); if (name?.trim()) void explorer.createTextFile(name.trim()).catch(() => window.alert("Unable to create text file.")); }}><span className="explorer-new-icon text" aria-hidden="true">T</span><span><b>Text file</b><small>Create a TXT file</small></span></button>
              <div className="explorer-new-menu-divider" role="separator" />
            </>}
            <button type="button" role="menuitem" onClick={() => { setNewMenuOpen(false); uploadInputRef.current?.click(); }}><span className="explorer-new-icon upload-icon" aria-hidden="true">^</span><span><b>Upload files</b><small>Choose one or more files</small></span></button>
          </div>}
          <input ref={uploadInputRef} hidden type="file" multiple onChange={event => {
            const files = Array.from(event.target.files || []);
            if (files.length) void explorer.uploadFiles(files);
            event.currentTarget.value = "";
          }} />
        </div>
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
                {folderNoteAvailable && <button
                  type="button"
                  className="folder-note-trigger"
                  onClick={() => setFolderNoteOpen(true)}
                  aria-label="Open folder note"
>{folderNoteSummary || "+ Add note"}</button>}
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
              <small>{!explorer.query.trim()
                ? !explorer.visibilityFilterReady
                  ? "Loading asset labels"
                  : explorer.visibilityFilter === "all"
                    ? explorer.items.length + " items"
                    : explorer.visibleItems.length + " items shown"
                : searchMediaMode === "all"
                  ? searchBusy
                    ? "Searching images & videos..."
                    : explorer.visibleItems.length + " images / " + videoSearch.total + " videos"
                  : searchMediaMode === "videos"
                    ? videoSearch.loading
                      ? "Searching indexed videos..."
                      : videoSearch.total + " video result" + (videoSearch.total === 1 ? "" : "s")
                    : explorer.searching
                      ? "Searching with Search V3..."
                      : explorer.searchComplete
                        ? "Completed: " + explorer.visibleItems.length + " results"
                        : explorer.visibleItems.length + " results"}</small>
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

          <div id="search-results">
          {searchMediaMode === "videos" && explorer.query.trim() ? videoResults : <>
          {searchMediaMode === "all" && explorer.query.trim() && <h2 className="mixed-search-heading"><button type="button" className="mixed-search-toggle" aria-expanded={imageResultsExpanded} aria-controls="mixed-image-results" onClick={() => setImageResultsExpanded(value => !value)}><span>Images <small>{explorer.searching ? "Searching..." : explorer.searchV3.total + " results"}</small></span><i aria-hidden="true">{imageResultsExpanded ? "−" : "+"}</i></button></h2>}
          <div id="mixed-image-results" hidden={searchMediaMode === "all" && Boolean(explorer.query.trim()) && !imageResultsExpanded}>
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
            onContextMenu={(item, event) => { event.preventDefault(); setAssetContextMenu({ item, position: { x: event.clientX, y: event.clientY } }); }}
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
          </div>
          {searchMediaMode === "all" && explorer.query.trim() && <section className="mixed-search-section" aria-label="Video results">
            <h2><button type="button" className="mixed-search-toggle" aria-expanded={videoResultsExpanded} aria-controls="mixed-video-results" onClick={() => setVideoResultsExpanded(value => !value)}><span>Videos <small>{videoSearch.loading ? "Searching..." : videoSearch.total + " results"}</small></span><i aria-hidden="true">{videoResultsExpanded ? "−" : "+"}</i></button></h2>
            <div id="mixed-video-results" hidden={!videoResultsExpanded}>{videoResults}</div>
          </section>}
          </>}
          </div>
          {(!explorer.query.trim() || imageSearchEnabled) && explorer.selected.size > 0 && <div className="bulk">
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

    {assetContextMenu && <AssetContextMenu
      item={assetContextMenu.item}
      position={assetContextMenu.position}
      onOpen={() => openContextItem(assetContextMenu.item)}
      onDownload={() => {
        const link = document.createElement("a");
        link.href = explorerAssetUrl(assetContextMenu.item, "media");
        link.download = assetContextMenu.item.name;
        link.click();
      }}
      onCopy={() => copyContextItem(assetContextMenu.item)}
      onMove={() => moveContextItem(assetContextMenu.item)}
      onDetails={() => openDetails(assetContextMenu.item)}
      onDelete={() => deleteContextItem(assetContextMenu.item)}
      onClose={() => setAssetContextMenu(null)}
    />}
    {previewItem && <MediaViewer item={previewItem} onClose={() => setPreviewItem(null)} />}
    {playbackItem && <VideoSearchPlayer item={playbackItem} onClose={() => setPlaybackItem(null)} />}
    {folderNoteOpen && explorer.path.at(-1) && <FolderNoteDrawer
      folderId={explorer.path.at(-1)!.id}
      folderName={explorer.path.at(-1)!.name}
      provider={explorer.provider}
      externalSourceId={explorer.activeExternalSourceId}
      canManage={!explorer.pureViewer}
      onClose={() => setFolderNoteOpen(false)}
      onSaved={note => setFolderNoteSummary(folderNotePreview(note.content_markdown))}
    />}
    {detailsOpen && explorer.auth.authenticated && <AssetDetailsPanel
      item={detailsItem}
      assetId={detailsAssetId}
      metadata={detailsItem ? explorer.metadataByItem[detailsItem.id] : undefined}
      onPreview={setPreviewItem}
      onClose={closeDetails}
      onOpenFolder={(folderId) => void explorer.openFolder(folderId)}
      canManageContent={explorer.provider === "google-drive" && !explorer.pureViewer}
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
    {confirm && createPortal(<div
      className="confirm-dialog-backdrop"
      onMouseDown={event => event.target === event.currentTarget && setConfirm(null)}
    >
      <section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-message">
        <span className="confirm-dialog-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M12 3 2.8 19h18.4L12 3Z" /><path d="M12 9v4m0 3h.01" /></svg>
        </span>
        <div className="confirm-dialog-copy">
          <span>CONFIRMATION</span>
          <h2 id="confirm-dialog-title">Confirm action</h2>
          <p id="confirm-dialog-message">{confirm.message}</p>
        </div>
        <div className="confirm-dialog-actions">
          <button type="button" className="secondary" onClick={() => setConfirm(null)}>Cancel</button>
          <button type="button" className="primary" autoFocus onClick={confirm.run}>Confirm</button>
        </div>
      </section>
    </div>, document.body)}
    <AnalyzeMetadataDialog
      open={analyzeOpen}
      assetIds={analysisAssetIds}
      sourceProvider={explorer.provider}
      authorized={completeAnalysisSelection && analysisAssetIds.length > 0}
      onClose={() => setAnalyzeOpen(false)}
    />
  </main>;
}
