import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Asset,
  AssetMetadata,
  AssetMetadataMap,
  AuthState,
  CloudUser,
  DriveIndexStatus,
  Folder,
  OAuthErrorState,
  Provider,
  ProviderSessions,
  ConnectedSource,
  Tag,
  TreeCache,
  VisibilityFilter,
  ViewerBootstrap,
  ViewerBootstrapSource,
} from "../types";
import { isSearchRequestInFlight, useSearchV3 } from "./useSearchV3";

const emptyIndexStatus: DriveIndexStatus = {
  state: "idle",
  status: "Waiting to index Google Drive",
  progress: 0,
  indexed_count: 0,
  processed_folders: 0,
  pending_folders: 0,
  skipped_folders: 0,
};

const rootId = (provider: Provider) => provider === "sharepoint" ? "sharepoint-root" : provider === "onedrive" ? "onedrive-root" : "root";
const explorerLocationKey = (provider: Provider) => "creative-asset-manager:explorer-location:" + provider;

export function folderIdFromPath(pathname: string): string | null {
  const match = /^\/folder\/([^/]+)\/?$/.exec(pathname);
  if (!match) return null;
  try {
    const folderId = decodeURIComponent(match[1]);
    return folderId.trim() ? folderId : null;
  } catch {
    return null;
  }
}

export function folderPath(folderId: string): string {
  return "/folder/" + encodeURIComponent(folderId);
}

type SavedExplorerLocation = {
  version: 3;
  provider: Provider;
  external_source_id: string;
  assigned_root_id: string;
  saved_at: number;
  path: Array<Pick<Asset, "id" | "name" | "kind" | "mime_type" | "provider" | "external_source_id">>;
};

export function parseSavedExplorerLocation(
  value: string | null,
  provider: Provider,
): SavedExplorerLocation | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<SavedExplorerLocation>;
    if (
      parsed.version !== 3
      || parsed.provider !== provider
      || typeof parsed.external_source_id !== "string" || !parsed.external_source_id.trim()
      || typeof parsed.assigned_root_id !== "string" || !parsed.assigned_root_id.trim()
      || typeof parsed.saved_at !== "number"
      || !Number.isFinite(parsed.saved_at)
      || !Array.isArray(parsed.path)
      || !parsed.path.length
    ) return null;
    const path = parsed.path.filter((item): item is SavedExplorerLocation["path"][number] => Boolean(
      item && item.provider === provider && item.kind === "folder"
      && typeof item.id === "string" && item.id.trim() && typeof item.name === "string",
    ));
    if (
      path.length !== parsed.path.length
      || path.some(item => item.external_source_id !== parsed.external_source_id)
      || !path.some(item => item.id === parsed.assigned_root_id)
    ) return null;
    return parsed as SavedExplorerLocation;
  } catch { return null; }
}

export function savedLocationIsAuthorized(saved: SavedExplorerLocation, bootstrap: ViewerBootstrap): boolean {
  const source = bootstrap.sources.find(item => item.external_source_id === saved.external_source_id);
  return Boolean(source?.folders.some(folder => folder.id === saved.assigned_root_id));
}

export function clearSavedExplorerLocation(provider: Provider): void {
  try {
    window.localStorage.removeItem(explorerLocationKey(provider));
  } catch { /* browser storage is optional */ }
}


function savedLocation(
  path: Asset[],
  provider: Provider,
  externalSourceId: string,
  assignedRootId: string,
): SavedExplorerLocation {
  return {
    version: 3,
    provider,
    external_source_id: externalSourceId,
    assigned_root_id: assignedRootId,
    saved_at: Date.now(),
    path: path.map(({ id, name, kind, mime_type, provider, external_source_id }) => ({ id, name, kind, mime_type, provider, external_source_id })),
  };
}

export type UploadState = "queued" | "uploading" | "completed" | "failed";
export type UploadItem = { id: string; name: string; status: UploadState; error?: string };

export function apiErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (!payload || typeof payload !== "object") return fallback;

  const record = payload as { detail?: unknown; message?: unknown };
  if (typeof record.message === "string" && record.message.trim()) return record.message;
  if (typeof record.detail === "string" && record.detail.trim()) return record.detail;
  if (record.detail && typeof record.detail === "object") {
    const detail = record.detail as { message?: unknown };
    if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
  }
  return fallback;
}

export function uploadErrorMessage(payload: unknown, fallback = "Upload failed. Try again."): string {
  return apiErrorMessage(payload, fallback);
}

export function isPureViewerIdentity(identity: { roles?: string[]; is_processing_admin?: boolean }): boolean {
  const roles = new Set(identity.roles || []);
  return roles.has("viewer")
    && !identity.is_processing_admin
    && !["operator", "tenant_admin", "billing_admin"].some(role => roles.has(role));
}

export function pruneSelectedIds(selected: ReadonlySet<string>, visibleItems: Asset[]): Set<string> {
  const visibleIds = new Set(visibleItems.map(item => item.id));
  const next = new Set([...selected].filter(id => visibleIds.has(id)));
  return next.size === selected.size ? selected as Set<string> : next;
}

export function appendUniqueFolderPage(current: Asset[], incoming: Asset[]): Asset[] {
  const existingIds = new Set(current.map(item => item.id));
  const appended = incoming.filter(item => !existingIds.has(item.id));
  return appended.length ? [...current, ...appended] : current;
}

const oauthMessages: Record<string, string> = {
  denied: "Google access was cancelled or denied.",
  incomplete: "Google returned an incomplete authorization response.",
  state: "The sign-in request expired. Please start again.",
  token_exchange: "Google could not complete the secure token exchange.",
  scope: "Google Drive read/write permission was not granted. Reconnect the Drive source and approve access.",
  profile: "The cloud account connected, but its profile could not be loaded.",
  self_signup_disabled: "New application users are not enabled. Ask an administrator to provision your account.",
  email_domain_not_allowed: "This email domain is not allowed to access the application.",
  tenant_membership_required: "Your account does not have an active workspace membership.",
  default_tenant_unavailable: "The configured default workspace is unavailable.",
  account_inactive: "Your application account is suspended or disabled.",
};

export function oauthMessageFor(errorCode: string): string {
  return oauthMessages[errorCode] || "Cloud sign-in could not be completed.";
}

export function useDriveExplorer(imageSearchEnabled = true) {
  const [provider, setProvider] = useState<Provider>("google-drive");
  const [activeExternalSourceId, setActiveExternalSourceId] = useState<string | null>(null);
  const [activeAssignedRootId, setActiveAssignedRootId] = useState<string | null>(null);
  const [viewerBootstrap, setViewerBootstrap] = useState<ViewerBootstrap | null>(null);
  const [viewerBootstrapState, setViewerBootstrapState] = useState<"idle" | "loading" | "ready" | "permission" | "error">("idle");
  const [pureViewer, setPureViewer] = useState<boolean | null>(null);
  const [explorerReady, setExplorerReady] = useState(false);
  const [applicationAuthenticated, setApplicationAuthenticated] = useState<boolean | null>(null);
  const [applicationUser, setApplicationUser] = useState<CloudUser | null>(null);
  const [applicationAuthProvider, setApplicationAuthProvider] = useState<"google" | "microsoft" | null>(null);
  const [authByProvider, setAuthByProvider] = useState<ProviderSessions>({
    "google-drive": { authenticated: false, user: null, checking: true },
    onedrive: { authenticated: false, user: null, checking: true },
    sharepoint: { authenticated: false, user: null, checking: true },
  });
  const [sources, setSources] = useState<ConnectedSource[]>([]);
  const auth = authByProvider[provider];
  const [path, setPath] = useState<Asset[]>([]);
  const [items, setItems] = useState<Asset[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [metadataByItem, setMetadataByItem] = useState<AssetMetadataMap>({});
  const [visibilityFilter, setVisibilityFilter] = useState<VisibilityFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [childrenByParent, setChildrenByParent] = useState<TreeCache>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [loadingTreeIds, setLoadingTreeIds] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("q") || "");
  const [loading, setLoading] = useState(true);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [error, setError] = useState("");
  const [oauthError, setOauthError] = useState<OAuthErrorState>(null);
  const [metadataIndex, setMetadataIndex] = useState<DriveIndexStatus>({ ...emptyIndexStatus });
  const searchV3 = useSearchV3(auth.authenticated && explorerReady, provider, imageSearchEnabled ? query : "", activeExternalSourceId, `${activeAssignedRootId || ""}:${visibilityFilter}`);

  const folderCache = useRef(new Map<string, Folder>());
  const folderRequests = useRef(new Map<string, Promise<Folder>>());
  const treeFolderCache = useRef(new Map<string, Asset[]>());
  const treeFolderRequests = useRef(new Map<string, Promise<Asset[]>>());
  const prefetchTimer = useRef<number | undefined>(undefined);
  const openSequence = useRef(0);
  const paginationSequence = useRef(0);
  const loadedPageTokens = useRef(new Set<string>());
  const loadMoreRequest = useRef<Promise<void> | null>(null);
  const loadMoreAbortController = useRef<AbortController | null>(null);
  const browseControllers = useRef(new Set<AbortController>());
  const [nextPageToken, setNextPageToken] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState("");

  function resetFolderPagination() {
    paginationSequence.current += 1;
    loadedPageTokens.current.clear();
    loadMoreAbortController.current?.abort();
    loadMoreAbortController.current = null;
    loadMoreRequest.current = null;
    setNextPageToken(null);
    setHasMore(false);
    setLoadingMore(false);
    setLoadMoreError("");
  }

  function newBrowseController() {
    const controller = new AbortController();
    browseControllers.current.add(controller);
    return controller;
  }

  function releaseBrowseController(controller: AbortController) {
    browseControllers.current.delete(controller);
  }

  function abortPendingBrowse() {
    browseControllers.current.forEach(controller => controller.abort());
    browseControllers.current.clear();
  }

  const folderCacheKey = (source: Provider, id: string, sourceId: string | null = activeExternalSourceId) => source + ":" + (sourceId || "") + ":" + id;

  function invalidateFolderPages(id: string, source: Provider = provider, sourceId: string | null = activeExternalSourceId) {
    const prefix = folderCacheKey(source, id, sourceId);
    for (const key of folderCache.current.keys()) {
      if (key === prefix || key.startsWith(prefix + ":page:")) folderCache.current.delete(key);
    }
  }

  async function fetchFolder(
    id: string,
    source: Provider = provider,
    pageToken?: string,
    signal?: AbortSignal,
    externalSourceId: string | null = activeExternalSourceId,
  ): Promise<Folder> {
    if (pureViewer && !externalSourceId) {
      throw Error("Select an assigned source before browsing");
    }
    const baseKey = folderCacheKey(source, id, externalSourceId);
    const key = pageToken ? baseKey + ":page:" + pageToken : baseKey;
    const cached = folderCache.current.get(key);
    if (cached) return cached;

    const pending = folderRequests.current.get(key);
    if (pending) return pending;

    const request = (async () => {
      const params = new URLSearchParams({
        parent_id: id,
        provider: source,
        page_size: "100",
      });
      if (pageToken) params.set("page_token", pageToken);
      if (externalSourceId) params.set("external_source_id", externalSourceId);
      const response = await fetch("/api/explorer/children?" + params.toString(), { signal });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        throw Error(apiErrorMessage(body, "Unable to load folder"));
      }
      const folder = await response.json() as Folder;
      folderCache.current.set(key, folder);
      return folder;
    })();

    folderRequests.current.set(key, request);
    try {
      return await request;
    } finally {
      folderRequests.current.delete(key);
    }
  }

  function cacheFolders(id: string, children: Asset[], source: Provider = provider, sourceId: string | null = activeExternalSourceId) {
    const folders = children.filter(item => item.kind === "folder");
    treeFolderCache.current.set(folderCacheKey(source, id, sourceId), folders);
    setChildrenByParent(current => ({ ...current, [id]: folders }));
  }

  function hydrateTreePath(nodes: Asset[], source: Provider = provider) {
    if (nodes.length < 2) return;

    setChildrenByParent(current => {
      const next = { ...current };
      for (let index = 0; index < nodes.length - 1; index += 1) {
        const parent = nodes[index];
        const child = nodes[index + 1];
        const children = next[parent.id] ?? [];
        const merged = children.some(item => item.id === child.id)
          ? children
          : [...children, child];
        next[parent.id] = merged;
      }
      return next;
    });
    setExpanded(current => {
      const next = new Set(current);
      nodes.slice(0, -1).forEach(node => next.add(node.id));
      return next;
    });
  }

  async function refreshTreePath(
    nodes: Asset[],
    source: Provider,
    sourceId: string | null,
    requestSequence: number,
    signal: AbortSignal,
  ) {
    await Promise.all(nodes.map(async node => {
      const folders = await fetchTreeFolders(node.id, source, sourceId, signal);
      if (!signal.aborted && requestSequence === openSequence.current) {
        cacheFolders(node.id, folders, source, sourceId);
      }
    }));
  }

  async function fetchTreeFolders(id: string, source: Provider = provider, sourceId: string | null = activeExternalSourceId, signal?: AbortSignal): Promise<Asset[]> {
    if (pureViewer && !sourceId) {
      throw Error("Select an assigned source before browsing");
    }
    const key = folderCacheKey(source, id, sourceId);
    const cached = treeFolderCache.current.get(key);
    if (cached) return cached;

    const pending = treeFolderRequests.current.get(key);
    if (pending) return pending;

    const request = (async () => {
      const params = new URLSearchParams({ parent_id: id, provider: source });
      if (sourceId) params.set("external_source_id", sourceId);
      const response = await fetch("/api/explorer/folders?" + params.toString(), { signal });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        throw Error(apiErrorMessage(body, "Unable to load folders"));
      }
      const folders = await response.json() as Asset[];
      treeFolderCache.current.set(key, folders);
      return folders;
    })();

    treeFolderRequests.current.set(key, request);
    try {
      return await request;
    } finally {
      treeFolderRequests.current.delete(key);
    }
  }

  function cancelFolderPrefetch() {
    window.clearTimeout(prefetchTimer.current);
    prefetchTimer.current = undefined;
  }

  function scheduleFolderPrefetch(id: string) {
    const key = folderCacheKey(provider, id);
    if (folderCache.current.has(key) || folderRequests.current.has(key)) return;
    cancelFolderPrefetch();
    prefetchTimer.current = window.setTimeout(() => {
      const controller = newBrowseController();
      void fetchFolder(id, provider, undefined, controller.signal)
        .catch(() => undefined)
        .finally(() => releaseBrowseController(controller));
    }, 180);
  }

  async function open(id = rootId(provider), ancestors: Asset[] = [], source: Provider = provider, preserveSelection = false, sourceId: string | null = activeExternalSourceId) {
    const requestSequence = ++openSequence.current;
    const controller = newBrowseController();
    resetFolderPagination();
    const cached = folderCache.current.has(folderCacheKey(source, id, sourceId));
    setLoading(!cached);
    setError("");
    if (!preserveSelection) setSelected(new Set());
    cancelFolderPrefetch();

    try {
      const folder = await fetchFolder(id, source, undefined, controller.signal, sourceId);
      if (requestSequence !== openSequence.current || controller.signal.aborted) return false;
      const nextPath = [...ancestors, folder.parent];
      setItems(folder.children);
      const treeSourceId = folder.parent.external_source_id ?? sourceId ?? null;
      setActiveExternalSourceId(treeSourceId);
      // A breadcrumb navigation reconstructs only the selected path. Load the
      // complete folder list for every expanded level so the sidebar keeps all
      // siblings instead of displaying only the reconstructed path nodes.
      void refreshTreePath(nextPath, source, treeSourceId, requestSequence, controller.signal)
        .catch(() => undefined);
      if (pureViewer && sourceId) {
        const assignedRoot = viewerBootstrap?.sources
          .find(item => item.external_source_id === sourceId)
          ?.folders.find(item => item.id === id);
        if (assignedRoot) setActiveAssignedRootId(assignedRoot.id);
      }
      setPath(nextPath);
      setNextPageToken(folder.next_page_token || null);
      setHasMore(Boolean(folder.has_more && folder.next_page_token));
      hydrateTreePath(nextPath, source);
      // Tree expansion needs a complete folder listing. Never put an interactive
      // first page into the tree cache, otherwise folders after page one disappear.
      if (!folder.has_more) cacheFolders(id, folder.children, source, sourceId);
      else treeFolderCache.current.delete(folderCacheKey(source, id, sourceId));
      setExpanded(current => new Set(current).add(id));
      return true;
    } catch (reason) {
      if (requestSequence === openSequence.current && !controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Unable to load folder");
      }
      return false;
    } finally {
      releaseBrowseController(controller);
      if (requestSequence === openSequence.current) setLoading(false);
    }
  }

  async function loadMoreFolderItems() {
    const currentFolder = path.at(-1);
    const token = nextPageToken;
    if (
      !currentFolder
      || !token
      || !hasMore
      || loadingMore
      || loadMoreRequest.current
      || loadedPageTokens.current.has(token)
    ) return;

    const requestSequence = openSequence.current;
    const pageSequence = paginationSequence.current;
    const controller = new AbortController();
    loadMoreAbortController.current = controller;
    loadedPageTokens.current.add(token);
    setLoadingMore(true);
    setLoadMoreError("");

    const request = (async () => {
      try {
        const page = await fetchFolder(currentFolder.id, provider, token, controller.signal, currentFolder.external_source_id || activeExternalSourceId);
        if (
          controller.signal.aborted
          || requestSequence !== openSequence.current
          || pageSequence !== paginationSequence.current
        ) return;

        setItems(current => appendUniqueFolderPage(current, page.children));
        setNextPageToken(page.next_page_token || null);
        setHasMore(Boolean(page.has_more && page.next_page_token));
      } catch (reason) {
        if (!controller.signal.aborted && pageSequence === paginationSequence.current) {
          loadedPageTokens.current.delete(token);
          setLoadMoreError(reason instanceof Error ? reason.message : "Unable to load more items");
        }
      } finally {
        if (loadMoreAbortController.current === controller) loadMoreAbortController.current = null;
        if (pageSequence === paginationSequence.current) setLoadingMore(false);
      }
    })();
    loadMoreRequest.current = request;
    try {
      await request;
    } finally {
      if (loadMoreRequest.current === request) loadMoreRequest.current = null;
    }
  }

  /** Navigate from an explicit folder selection and leave search mode behind. */
  async function openFolder(id = rootId(provider), ancestors: Asset[] = [], source: Provider = provider) {
    setQuery("");
    searchV3.clearSearchFilters();
    const params = new URLSearchParams(window.location.search);
    params.delete("q");
    [...params.keys()].filter(key => key.startsWith("facet.")).forEach(key => params.delete(key));
    const cleanQuery = params.toString();
    const opened = await open(id, ancestors, source);
    if (opened) {
      const nextUrl = folderPath(id) + (cleanQuery ? "?" + cleanQuery : "");
      if (window.location.pathname + window.location.search === nextUrl) window.history.replaceState({}, "", nextUrl);
      else window.history.pushState({}, "", nextUrl);
    }
  }

  async function hydrateExplorerRoot(
    source: Provider,
    externalSourceId: string | null,
  ): Promise<void> {
    const id = rootId(source);
    setExpanded(current => new Set(current).add(id));
    setLoadingTreeIds(current => new Set(current).add(id));
    try {
      const roots = await fetchTreeFolders(id, source, externalSourceId);
      cacheFolders(id, roots, source, externalSourceId);
    } finally {
      setLoadingTreeIds(current => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }

  async function restoreUrlLocation(source: Provider) {
    const folderId = folderIdFromPath(window.location.pathname);
    if (!folderId) return false;

    // Keep the complete persisted path when it belongs to this URL; this is
    // both faster and preserves every expanded sidebar branch on reload.
    const saved = parseSavedExplorerLocation(
      window.localStorage.getItem(explorerLocationKey(source)), source,
    );
    if (saved?.path.at(-1)?.id === folderId) {
      return restoreSavedLocation(source);
    }

    // A shared/deep link may not have local state. Resolve the server-side
    // breadcrumb first so the selected folder and all of its source-tree
    // ancestors are hydrated instead of showing an empty sidebar tree.
    try {
      const folder = await fetchFolder(folderId, source);
      const externalSourceId = folder.parent.external_source_id || null;
      const params = new URLSearchParams({ provider: source });
      if (externalSourceId) params.set("external_source_id", externalSourceId);
      const response = await fetch(
        "/api/explorer/items/" + encodeURIComponent(folderId) + "/location?" + params.toString(),
      );
      const location = response.ok
        ? await response.json() as { status?: string; breadcrumb?: Array<{ id: string; name: string }> }
        : null;
      const ancestors = location?.status === "available" && Array.isArray(location.breadcrumb)
        ? location.breadcrumb.map(node => ({
          provider: source,
          id: node.id,
          name: node.name,
          kind: "folder" as const,
          mime_type: "application/vnd.google-apps.folder",
          external_source_id: externalSourceId || undefined,
        }))
        : [];
      const opened = await open(folderId, ancestors, source, false, externalSourceId);
      if (opened) await hydrateExplorerRoot(source, externalSourceId);
      return opened;
    } catch {
      return false;
    }
  }

  async function restoreSavedLocation(source: Provider, bootstrap?: ViewerBootstrap) {
    const saved = parseSavedExplorerLocation(window.localStorage.getItem(explorerLocationKey(source)), source);
    if (!saved || (bootstrap && !savedLocationIsAuthorized(saved, bootstrap))) {
      clearSavedExplorerLocation(source);
      return false;
    }
    const controller = newBrowseController();
    try {
      const savedSourceId = saved.external_source_id;
      setActiveExternalSourceId(savedSourceId);
      setActiveAssignedRootId(saved.assigned_root_id);
      const restoredPath: Asset[] = [];
      for (const item of saved.path) {
        const folder = await fetchFolder(item.id, source, undefined, controller.signal, savedSourceId);
        if (restoredPath.at(-1)?.id !== folder.parent.id) restoredPath.push(folder.parent);
        if (!folder.has_more) cacheFolders(item.id, folder.children, source, savedSourceId);
      }
      const current = restoredPath.at(-1);
      if (!current) throw Error("Saved folder is unavailable");
      setExpanded(new Set(restoredPath.slice(0, -1).map(folder => folder.id)));
      const opened = await open(current.id, restoredPath.slice(0, -1), source, false, savedSourceId);
      if (opened) await hydrateExplorerRoot(source, savedSourceId);
      return opened;
    } catch {
      clearSavedExplorerLocation(source);
      return false;
    } finally {
      releaseBrowseController(controller);
    }
  }

  function showAssignedRoots(source: Provider, selectedSource: ViewerBootstrapSource) {
    const sourceId = selectedSource.external_source_id;
    const folders: Asset[] = selectedSource.folders.map(folder => ({
      provider: source,
      id: folder.id,
      name: folder.name,
      kind: "folder",
      mime_type: "application/vnd.google-apps.folder",
      external_source_id: sourceId,
      has_children: true,
    }));
    const virtualRoot: Asset = {
      provider: source,
      id: rootId(source),
      name: selectedSource.display_name,
      kind: "folder",
      mime_type: "application/vnd.google-apps.folder",
      external_source_id: sourceId,
      has_children: true,
    };
    setPath([virtualRoot]);
    setItems(folders);
    setChildrenByParent({ [virtualRoot.id]: folders });
    setExpanded(new Set([virtualRoot.id]));
    setLoading(false);
  }

  async function activateViewerSource(bootstrap: ViewerBootstrap, sourceId: string, source: Provider) {
    const selectedSource = bootstrap.sources.find(item => item.external_source_id === sourceId);
    if (!selectedSource) return;
    clearExplorer(source);
    setViewerBootstrap(bootstrap);
    setViewerBootstrapState("ready");
    setActiveExternalSourceId(sourceId);
    const saved = parseSavedExplorerLocation(window.localStorage.getItem(explorerLocationKey(source)), source);
    if (saved && saved.external_source_id === sourceId && savedLocationIsAuthorized(saved, bootstrap)) {
      if (await restoreSavedLocation(source, bootstrap)) {
        setExplorerReady(true);
        return;
      }
    }
    clearSavedExplorerLocation(source);
    if (selectedSource.folders.length === 1) {
      const assignedRoot = selectedSource.folders[0];
      setActiveAssignedRootId(assignedRoot.id);
      if (await open(assignedRoot.id, [], source, false, sourceId)) setExplorerReady(true);
      return;
    }
    setActiveAssignedRootId(null);
    showAssignedRoots(source, selectedSource);
    setExplorerReady(true);
  }

  async function loadViewerBootstrap(source: Provider) {
    setExplorerReady(false);
    setViewerBootstrapState("loading");
    setLoading(true);
    try {
      const response = await fetch("/api/explorer/viewer/bootstrap?provider=" + encodeURIComponent(source));
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw Object.assign(Error(apiErrorMessage(payload, "Unable to load assigned folders")), { status: response.status, payload });
      const bootstrap = payload as ViewerBootstrap;
      setViewerBootstrap(bootstrap);
      setViewerBootstrapState("ready");
      if (bootstrap.auto_selected_source_id) {
        await activateViewerSource(bootstrap, bootstrap.auto_selected_source_id, source);
      } else {
        clearExplorer(source);
        setViewerBootstrap(bootstrap);
        setViewerBootstrapState("ready");
      }
    } catch (reason) {
      const failure = reason as { status?: number; payload?: unknown; message?: string };
      clearExplorer(source);
      setViewerBootstrapState(failure.status === 403 ? "permission" : "error");
      setError(failure.message || "Unable to load assigned folders");
    } finally {
      setLoading(false);
    }
  }

  async function refreshCurrentFolder() {
    const currentFolder = path.at(-1);
    if (!currentFolder) return;

    const sourceId = currentFolder.external_source_id || activeExternalSourceId;
    invalidateFolderPages(currentFolder.id, provider, sourceId);
    treeFolderCache.current.delete(folderCacheKey(provider, currentFolder.id, sourceId));
    await open(currentFolder.id, path.slice(0, -1), provider, true, sourceId);
  }
  async function toggleTree(node: Asset) {
    if (expanded.has(node.id)) {
      setExpanded(current => {
        const next = new Set(current);
        next.delete(node.id);
        return next;
      });
      return;
    }

    const nodeSourceId = node.external_source_id || activeExternalSourceId
    const cached = treeFolderCache.current.get(folderCacheKey(provider, node.id, nodeSourceId));
    if (cached) {
      cacheFolders(node.id, cached, provider, nodeSourceId);
      setExpanded(current => new Set(current).add(node.id));
      return;
    }

    setExpanded(current => new Set(current).add(node.id));
    setLoadingTreeIds(current => new Set(current).add(node.id));
    setError("");
    const controller = newBrowseController();
    try {
      const folders = await fetchTreeFolders(node.id, provider, activeExternalSourceId, controller.signal);
      if (controller.signal.aborted) return;
      cacheFolders(node.id, folders, provider, nodeSourceId);
      setExpanded(current => new Set(current).add(node.id));
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to expand folder");
    } finally {
      releaseBrowseController(controller);
      setLoadingTreeIds(current => {
        const next = new Set(current);
        next.delete(node.id);
        return next;
      });
    }
  }

  async function uploadFiles(files: File[]) {
    const parentId = path.at(-1)?.id || rootId(provider);
    const queued: UploadItem[] = files.map((file, index) => ({
      id: String(Date.now()) + "-" + index,
      name: file.name,
      status: "queued",
    }));
    setUploads(current => [...current, ...queued]);
    for (let index = 0; index < queued.length; index += 1) {
      const entry = queued[index];
      const file = files[index];
      setUploads(current => current.map(item => item.id === entry.id
        ? { ...item, status: "uploading", error: undefined }
        : item));
      try {
        const response = await fetch(
          "/api/explorer/upload?parent_id=" + encodeURIComponent(parentId)
            + "&provider=" + encodeURIComponent(provider)
            + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : "")
            + "&filename=" + encodeURIComponent(file.name)
            + "&mime_type=" + encodeURIComponent(file.type || "application/octet-stream"),
          { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file },
        );
        if (!response.ok) {
          const payload: unknown = await response.json().catch(() => null);
          throw Error(uploadErrorMessage(payload));
        }
        setUploads(current => current.map(item => item.id === entry.id
          ? { ...item, status: "completed" }
          : item));
      } catch (reason) {
        const error = reason instanceof Error ? reason.message : "Upload failed. Try again.";
        setUploads(current => current.map(item => item.id === entry.id
          ? { ...item, status: "failed", error }
          : item));
      }
    }
    await refreshCurrentFolder();
  }
  async function createFolder(name: string) {
    const parentId = path.at(-1)?.id || rootId(provider);
    const response = await fetch("/api/explorer/folders?name=" + encodeURIComponent(name)
      + "&parent_id=" + encodeURIComponent(parentId)
      + "&provider=" + encodeURIComponent(provider)
      + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : ""), { method: "POST" });
    if (!response.ok) throw Error("Unable to create folder");
    await refreshCurrentFolder();
  }

  async function createTextFile(name: string, content = "") {
    const parentId = path.at(-1)?.id || rootId(provider);
    const filename = name.toLowerCase().endsWith(".txt") ? name : name + ".txt";
    const response = await fetch("/api/explorer/upload?parent_id=" + encodeURIComponent(parentId)
      + "&provider=" + encodeURIComponent(provider)
      + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : "")
      + "&filename=" + encodeURIComponent(filename) + "&mime_type=text%2Fplain", {
        method: "POST", headers: { "Content-Type": "text/plain" }, body: content || "\n",
      });
    if (!response.ok) throw Error("Unable to create text file");
    await refreshCurrentFolder();
  }

  async function deleteItem(itemId: string) { const response = await fetch("/api/explorer/items/" + encodeURIComponent(itemId) + "?provider=" + encodeURIComponent(provider) + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : ""), { method: "DELETE" }); if (!response.ok) throw Error("Unable to delete file"); await refreshCurrentFolder(); }
  async function renameItem(itemId: string, requestedName: string) {
    const name = requestedName.trim();
    if (!name) throw Error("File or folder name cannot be empty.");
    const response = await fetch(
      "/api/explorer/items/" + encodeURIComponent(itemId)
        + "?provider=" + encodeURIComponent(provider)
        + "&name=" + encodeURIComponent(name)
        + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : ""),
      { method: "PATCH" },
    );
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw Error(apiErrorMessage(payload, "Unable to rename this item."));
    const renamed = payload as { name?: string };
    await refreshCurrentFolder();
    return renamed.name || name;
  }
  async function moveItem(itemId: string, destinationParentId: string) { const response = await fetch("/api/explorer/items/" + encodeURIComponent(itemId) + "/move?provider=" + encodeURIComponent(provider) + "&destination_parent_id=" + encodeURIComponent(destinationParentId) + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : ""), { method: "POST" }); if (!response.ok) throw Error("Unable to move file"); await refreshCurrentFolder(); }
  async function copyItems(itemIds: string[], destinationParentId: string) {
    for (const itemId of itemIds) {
      const response = await fetch("/api/explorer/items/" + encodeURIComponent(itemId) + "/copy?provider=" + encodeURIComponent(provider) + "&destination_parent_id=" + encodeURIComponent(destinationParentId) + (activeExternalSourceId ? "&external_source_id=" + encodeURIComponent(activeExternalSourceId) : ""), { method: "POST" });
      if (!response.ok) throw Error("Unable to copy item");
    }
    await refreshCurrentFolder();
  }

  function clearExplorer(source: Provider = provider) {
    abortPendingBrowse();
    setExplorerReady(false);
    setPath([]);
    setActiveExternalSourceId(null);
    setActiveAssignedRootId(null);
    setItems([]);
    setChildrenByParent({});
    setExpanded(new Set([rootId(source)]));
    setSelected(new Set());
    setMetadataByItem({});
    setQuery("");
    setError("");
    setLoading(false);
    setLoadingTreeIds(new Set());
    resetFolderPagination();
    folderCache.current.clear();
    folderRequests.current.clear();
    treeFolderCache.current.clear();
    treeFolderRequests.current.clear();
    openSequence.current += 1;
    cancelFolderPrefetch();
  }

  useEffect(() => {
    if (!auth.authenticated || !explorerReady || !path.length || !activeExternalSourceId || !activeAssignedRootId) return;
    try {
      window.localStorage.setItem(explorerLocationKey(provider), JSON.stringify(
        savedLocation(path, provider, activeExternalSourceId, activeAssignedRootId),
      ));
    } catch { /* browser storage is optional */ }
  }, [auth.authenticated, explorerReady, path, provider, activeExternalSourceId, activeAssignedRootId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const errorCode = params.get("auth_error");
    const requestId = params.get("auth_request") || undefined;
    const preferred: Provider = params.has("microsoft") || params.get("auth_provider") === "microsoft" ? "sharepoint" : "google-drive";
    if (errorCode) setOauthError({ message: oauthMessageFor(errorCode), requestId });
    if (errorCode || params.has("google") || params.has("microsoft")) {
      ["auth_error", "auth_request", "auth_message", "auth_provider", "google", "microsoft"].forEach(key => params.delete(key));
      const cleanQuery = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (cleanQuery ? "?" + cleanQuery : ""));
    }
    type SessionRead = { state: Omit<AuthState, "checking">; error?: string };
    async function readSession(url: string): Promise<SessionRead> {
      try {
        const response = await fetch(url);
        if (response.status === 401) return { state: { authenticated: false, user: null } };
        if (!response.ok) return { state: { authenticated: false, user: null }, error: "Provider session returned " + response.status };
        const body = await response.json() as Omit<AuthState, "checking">;
        if (typeof body?.authenticated !== "boolean") return { state: { authenticated: false, user: null }, error: "Provider session response was malformed" };
        return { state: body };
      } catch {
        return { state: { authenticated: false, user: null }, error: "Provider session is temporarily unavailable" };
      }
    }
    async function readIdentity(): Promise<{ user_id: string; roles: string[]; permissions: string[]; application_auth_provider?: "google" | "microsoft" | null; is_processing_admin: boolean; display_name?: string | null; email?: string | null; avatar_url?: string | null }> {
      const response = await fetch("/api/v1/auth/identity");
      if (response.status === 401) throw Object.assign(new Error("unauthenticated"), { status: 401 });
      if (!response.ok) throw Object.assign(new Error("Unable to verify workspace access"), { status: response.status });
      return await response.json();
    }
    async function initialize() {
      let identity: { user_id: string; roles: string[]; permissions: string[]; application_auth_provider?: "google" | "microsoft" | null; is_processing_admin: boolean; display_name?: string | null; email?: string | null; avatar_url?: string | null };
      try {
        identity = await readIdentity();
        setApplicationAuthenticated(true);
        setApplicationUser({ id: identity.user_id, name: identity.display_name || undefined, email: identity.email || undefined, picture: identity.avatar_url || undefined });
        setApplicationAuthProvider(identity.application_auth_provider || null);
      } catch (reason) {
        const status = typeof reason === "object" && reason && "status" in reason ? Number((reason as { status?: number }).status) : 0;
        setApplicationAuthenticated(status === 401 ? false : null);
        setApplicationUser(null);
        setApplicationAuthProvider(null);
        setAuthByProvider({
          "google-drive": { authenticated: false, user: null, checking: false },
          onedrive: { authenticated: false, user: null, checking: false },
          sharepoint: { authenticated: false, user: null, checking: false },
        });
        clearExplorer(provider);
        if (status !== 401) setError("Unable to verify application authentication. Please retry.");
        return;
      }

      let connectedSources: ConnectedSource[] = [];
      try {
        const sourceResponse = await fetch("/api/sources");
        if (!sourceResponse.ok) throw new Error("Unable to load connected sources");
        connectedSources = await sourceResponse.json() as ConnectedSource[];
      } catch {
        setError("Unable to load connected cloud sources.");
      }
      setSources(connectedSources);
      const activeByProvider = (item: Provider) => connectedSources.find(source =>
        source.status === "active" && ((item === "google-drive" && source.source_type === "google_drive") || source.source_type === item)
      );
      const sessions: ProviderSessions = {
        "google-drive": { authenticated: Boolean(activeByProvider("google-drive")), user: null, checking: false },
        onedrive: { authenticated: Boolean(activeByProvider("onedrive")), user: null, checking: false },
        sharepoint: { authenticated: Boolean(activeByProvider("sharepoint")), user: null, checking: false },
      };
      setAuthByProvider(sessions);
      const activeSource = activeByProvider(preferred) || connectedSources.find(source => source.status === "active") || null;
      const selected: Provider = activeSource ? (activeSource.source_type === "google_drive" ? "google-drive" : activeSource.source_type) : preferred;
      setProvider(selected);
      setActiveExternalSourceId(activeSource?.id || null);
      setPureViewer(isPureViewerIdentity(identity));
      if (!sessions[selected].authenticated) {
        setPureViewer(null);
        clearExplorer(selected);
        return;
      }
      try {
        const viewer = isPureViewerIdentity(identity);
        setPureViewer(viewer);
        if (viewer) {
          await loadViewerBootstrap(selected);
          return;
        }
        const restored = await restoreUrlLocation(selected) || await restoreSavedLocation(selected);
        if (!restored) {
          setActiveAssignedRootId(rootId(selected));
          await open(rootId(selected), [], selected, false, activeSource?.id || null);
        }
        setExplorerReady(true);
      } catch (reason) {
        clearExplorer(selected);
        setError(reason instanceof Error ? reason.message : "Unable to initialize workspace");
      }
    }
    void initialize();
    fetch("/api/tags").then(response => response.json()).then(setTags).catch(() => setTags([]));
    return () => { abortPendingBrowse(); openSequence.current += 1; cancelFolderPrefetch(); };
  }, []);

  useEffect(() => {
    // Search V3 is index-backed; normal browsing never starts or polls the legacy index.
    setMetadataIndex({ ...emptyIndexStatus });
  }, [auth.authenticated, provider]);

  async function selectProvider(source: Provider) {
    const selectedSource = sources.find(item => item.status === "active" && (source === "google-drive" ? item.source_type === "google_drive" : item.source_type === source));
    setProvider(source); setActiveExternalSourceId(selectedSource?.id || null); setOauthError(null); clearExplorer(source);
    if (!selectedSource) return;
    if (pureViewer) {
      await loadViewerBootstrap(source);
      return;
    }
    const restored = await restoreUrlLocation(source) || await restoreSavedLocation(source);
    if (!restored) {
      setActiveAssignedRootId(rootId(source));
      await open(rootId(source), [], source, false, selectedSource.id);
    }
    setExplorerReady(true);
  }

  async function selectSource(sourceId: string) {
    const selectedSource = sources.find(item => item.id === sourceId && item.status === "active");
    if (!selectedSource) return;
    const nextProvider: Provider = selectedSource.source_type === "google_drive" ? "google-drive" : selectedSource.source_type;
    setProvider(nextProvider); setActiveExternalSourceId(selectedSource.id); clearExplorer(nextProvider);
    setActiveAssignedRootId(rootId(nextProvider));
    await open(rootId(nextProvider), [], nextProvider, false, selectedSource.id);
    setExplorerReady(true);
  }

  async function disconnectSource(sourceId: string) {
    const response = await fetch("/api/sources/" + encodeURIComponent(sourceId) + "/disconnect", { method: "POST" });
    if (!response.ok) throw Error("Unable to disconnect source");
    const disconnected = await response.json() as ConnectedSource;
    setSources(current => current.map(item => item.id === sourceId ? disconnected : item));
    if (activeExternalSourceId === sourceId) {
      setActiveExternalSourceId(null); clearExplorer(provider);
    }
  }

  async function selectViewerSource(sourceId: string) {
    if (!viewerBootstrap || !viewerBootstrap.sources.some(source => source.external_source_id === sourceId)) return;
    await activateViewerSource(viewerBootstrap, sourceId, provider);
  }

  async function logout() {
    if (!applicationAuthProvider) throw Error("Application session provider is unavailable.");
    await fetch(applicationAuthProvider === "microsoft" ? "/api/auth/microsoft/logout" : "/api/auth/google/logout", { method: "POST" });
    setApplicationAuthenticated(false);
    setApplicationUser(null);
    setAuthByProvider(current => ({ ...current, [provider]: { authenticated: false, user: null, checking: false } }));
    clearExplorer(provider);
    setViewerBootstrap(null);
    setViewerBootstrapState("idle");
    setPureViewer(null);
  }

  function toggleSelection(id: string) {
    setSelected(current => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function replaceSelection(ids: Iterable<string>) {
    setSelected(new Set(ids));
  }

  function mergeMetadata(metadata: AssetMetadata[]) {
    setMetadataByItem(current => {
      const next = { ...current };
      metadata.forEach(item => {
        next[item.item_id] = item;
      });
      return next;
    });
  }

  async function applyTag(tagId: string) {
    setError("");
    const response = await fetch("/api/tags/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, item_ids: [...selected], tag_id: tagId }),
    });
    if (!response.ok) {
      setError("Unable to assign tag");
      return;
    }
    const body = await response.json() as { items: AssetMetadata[] };
    mergeMetadata(body.items);
    setSelected(new Set());
  }

  async function rateItems(itemIds: string[], rating: number | null) {
    setError("");
    const response = await fetch("/api/metadata/rating", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, item_ids: itemIds, rating }),
    });
    if (!response.ok) {
      setError("Unable to save asset rating");
      return false;
    }
    const body = await response.json() as { items: AssetMetadata[] };
    mergeMetadata(body.items);
    return true;
  }

  async function rateAsset(item: Asset, rating: number | null) {
    await rateItems([item.id], rating);
  }

  async function applyRating(rating: number | null) {
    if (await rateItems([...selected], rating)) setSelected(new Set());
  }

  function changeVisibilityFilter(filter: VisibilityFilter) {
    setVisibilityFilter(filter);
    setSelected(new Set());
  }

  const matchedItems = useMemo(
    () => searchV3.active && query.trim().length >= 1 ? searchV3.items : items,
    [items, query, searchV3.active, searchV3.items],
  );

  const visibleItems = useMemo(
    () => visibilityFilter === "all"
      ? matchedItems
      : matchedItems.filter(item =>
        item.kind === "folder"
        || metadataByItem[item.id]?.tag_ids.includes(visibilityFilter)
      ),
    [matchedItems, metadataByItem, visibilityFilter],
  );

  useEffect(() => {
    setSelected(current => pruneSelectedIds(current, visibleItems));
  }, [visibleItems]);

  const visibilityFilterReady = visibilityFilter === "all"
    || matchedItems.every(item =>
      item.kind === "folder" || metadataByItem[item.id] !== undefined
    );

  useEffect(() => {
    if (!auth.authenticated || matchedItems.length === 0) return;

    const controller = new AbortController();
    const itemIds = [...new Set(matchedItems.map(item => item.id))];

    async function loadMetadata() {
      try {
        const batches: string[][] = [];
        for (let index = 0; index < itemIds.length; index += 500) {
          batches.push(itemIds.slice(index, index + 500));
        }
        const metadata = await Promise.all(batches.map(async batch => {
          const response = await fetch("/api/metadata/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            signal: controller.signal,
            body: JSON.stringify({
              provider,
              item_ids: batch,
              ...(activeExternalSourceId ? { external_source_id: activeExternalSourceId } : {}),
            }),
          });
          if (!response.ok) throw Error("Unable to load asset metadata");
          const body = await response.json() as { items: AssetMetadata[] };
          return body.items;
        }));
        if (!controller.signal.aborted) mergeMetadata(metadata.flat());
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Unable to load asset metadata");
        }
      }
    }

    void loadMetadata();
    return () => controller.abort();
  }, [auth.authenticated, provider, matchedItems, activeExternalSourceId]);

  return {
    path,
    items,
    visibleItems,
    tags,
    metadataByItem,
    visibilityFilter,
    visibilityFilterReady,
    setVisibilityFilter: changeVisibilityFilter,
    selected,
    childrenByParent,
    expanded,
    loadingTreeIds,
    query,
    setQuery,
    searching: isSearchRequestInFlight(query, searchV3.loading),
    searchComplete: searchV3.active && query.trim().length >= 1 && !searchV3.loading && !searchV3.error,
    searchError: searchV3.error,
    searchDurationMs: searchV3.active ? searchV3.durationMs : null,
    loading,
    hasMoreFolderItems: hasMore,
    loadingMoreFolderItems: loadingMore,
    loadMoreFolderError: loadMoreError,
    loadMoreFolderItems,
    error,
    provider,
    auth,
    authByProvider,
    sources,
    selectSource,
    disconnectSource,
    applicationAuthenticated,
    applicationUser,
    oauthError,
    metadataIndex,
    explorerReady,
    pureViewer,
    viewerBootstrap,
    viewerBootstrapState,
    viewerSources: viewerBootstrap?.sources || [],
    selectViewerSource,
    searchReady: searchV3.capabilitiesResolved && searchV3.active,
    searchV3,
    retrySearch: searchV3.retry,
    refreshCurrentFolder,
    selectProvider,
    open,
    openFolder,
    toggleTree,
    scheduleFolderPrefetch,
    activeExternalSourceId,
    activeAssignedRootId,
    cancelFolderPrefetch,
    logout,
    toggleSelection,
    replaceSelection,
    applyTag,
    rateAsset,
    applyRating,
    clearSelection: () => setSelected(new Set()),
    uploads, uploadFiles, createFolder, createTextFile, deleteItem, renameItem, moveItem, copyItems, clearUploads: () => setUploads([]), currentFolderId: path.at(-1)?.id || rootId(provider),
  };
}
