import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Asset,
  AssetMetadata,
  AssetMetadataMap,
  AuthState,
  DriveIndexStatus,
  Folder,
  OAuthErrorState,
  Provider,
  ProviderSessions,
  SearchResponse,
  Tag,
  TreeCache,
  VisibilityFilter,
} from "../types";
import { searchAssets } from "../utils/searchAssets";
import { isSearchRequestInFlight, useSearchV2 } from "./useSearchV2";

type SearchStreamEvent = {
  type: "progress" | "result" | "error";
  status?: string;
  progress?: number;
  indexed_count?: number;
  processed_folders?: number;
  pending_folders?: number;
  detail?: string;
  data?: SearchResponse;
};

const emptyIndexStatus: DriveIndexStatus = {
  state: "idle",
  status: "Waiting to index Google Drive",
  progress: 0,
  indexed_count: 0,
  processed_folders: 0,
  pending_folders: 0,
  skipped_folders: 0,
};

const rootId = (provider: Provider) => provider === "sharepoint" ? "sharepoint-root" : "root";
const explorerLocationKey = (provider: Provider) => "creative-asset-manager:explorer-location:" + provider;
export const EXPLORER_LOCATION_MAX_AGE_MS = 15 * 60 * 1000;

type SavedExplorerLocation = {
  version: 1;
  saved_at: number;
  path: Array<Pick<Asset, "id" | "name" | "kind" | "mime_type" | "provider">>;
};

export function parseSavedExplorerLocation(
  value: string | null,
  provider: Provider,
  now = Date.now(),
): SavedExplorerLocation | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<SavedExplorerLocation>;
    if (
      parsed.version !== 1
      || typeof parsed.saved_at !== "number"
      || !Number.isFinite(parsed.saved_at)
      || now - parsed.saved_at >= EXPLORER_LOCATION_MAX_AGE_MS
      || !Array.isArray(parsed.path)
      || !parsed.path.length
    ) return null;
    const path = parsed.path.filter((item): item is SavedExplorerLocation["path"][number] => Boolean(
      item && item.provider === provider && item.kind === "folder"
      && typeof item.id === "string" && item.id.trim() && typeof item.name === "string",
    ));
    return path.length === parsed.path.length ? { version: 1, saved_at: parsed.saved_at, path } : null;
  } catch { return null; }
}

function savedLocation(path: Asset[]): SavedExplorerLocation {
  return {
    version: 1,
    saved_at: Date.now(),
    path: path.map(({ id, name, kind, mime_type, provider }) => ({ id, name, kind, mime_type, provider })),
  };
}

export type UploadState = "queued" | "uploading" | "completed" | "failed";
export type UploadItem = { id: string; name: string; status: UploadState; error?: string };

export function uploadErrorMessage(payload: unknown, fallback = "Upload failed. Try again."): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    return (detail as { message: string }).message;
  }
  return fallback;
}

export function pruneSelectedIds(selected: ReadonlySet<string>, visibleItems: Asset[]): Set<string> {
  const visibleIds = new Set(visibleItems.map(item => item.id));
  const next = new Set([...selected].filter(id => visibleIds.has(id)));
  return next.size === selected.size ? selected as Set<string> : next;
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

export function useDriveExplorer() {
  const [provider, setProvider] = useState<Provider>("google-drive");
  const [authByProvider, setAuthByProvider] = useState<ProviderSessions>({
    "google-drive": { authenticated: false, user: null, checking: true },
    sharepoint: { authenticated: false, user: null, checking: true },
  });
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
  const [searchResults, setSearchResults] = useState<Asset[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchProgress, setSearchProgress] = useState(0);
  const [searchStatus, setSearchStatus] = useState("Preparing search");
  const [searchProcessedFolders, setSearchProcessedFolders] = useState(0);
  const [searchPendingFolders, setSearchPendingFolders] = useState(0);
  const [searchIndexedCount, setSearchIndexedCount] = useState(0);
  const [searchIndexSource, setSearchIndexSource] = useState<"directus" | "memory" | null>(null);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [error, setError] = useState("");
  const [oauthError, setOauthError] = useState<OAuthErrorState>(null);
  const [metadataIndex, setMetadataIndex] = useState<DriveIndexStatus>({ ...emptyIndexStatus });
  const [indexRetryKey, setIndexRetryKey] = useState(0);
  const searchV2 = useSearchV2(auth.authenticated, provider, query);

  const folderCache = useRef(new Map<string, Folder>());
  const folderRequests = useRef(new Map<string, Promise<Folder>>());
  const treeFolderCache = useRef(new Map<string, Asset[]>());
  const treeFolderRequests = useRef(new Map<string, Promise<Asset[]>>());
  const prefetchTimer = useRef<number | undefined>(undefined);
  const openSequence = useRef(0);

  function resetSearch() {
    setSearchResults(null);
    setSearching(false);
    setSearchProgress(0);
    setSearchStatus("Preparing search");
    setSearchProcessedFolders(0);
    setSearchPendingFolders(0);
    setSearchIndexedCount(0);
    setSearchIndexSource(null);
    setSearchTruncated(false);
    setSearchError("");
  }

  async function fetchFolder(id: string, source: Provider = provider): Promise<Folder> {
    const key = source + ":" + id;
    const cached = folderCache.current.get(key);
    if (cached) return cached;

    const pending = folderRequests.current.get(key);
    if (pending) return pending;

    const request = (async () => {
      const response = await fetch("/api/explorer/children?parent_id=" + encodeURIComponent(id) + "&provider=" + encodeURIComponent(source));
      if (!response.ok) throw Error((await response.json()).detail);
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

  function cacheFolders(id: string, children: Asset[], source: Provider = provider) {
    const folders = children.filter(item => item.kind === "folder");
    treeFolderCache.current.set(source + ":" + id, folders);
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
        treeFolderCache.current.set(source + ":" + parent.id, merged);
      }
      return next;
    });
    setExpanded(current => {
      const next = new Set(current);
      nodes.slice(0, -1).forEach(node => next.add(node.id));
      return next;
    });
  }

  async function fetchTreeFolders(id: string, source: Provider = provider): Promise<Asset[]> {
    const key = source + ":" + id;
    const cached = treeFolderCache.current.get(key);
    if (cached) return cached;

    const fullFolder = folderCache.current.get(key);
    if (fullFolder) return fullFolder.children.filter(item => item.kind === "folder");

    const fullRequest = folderRequests.current.get(key);
    if (fullRequest) {
      const folder = await fullRequest;
      return folder.children.filter(item => item.kind === "folder");
    }

    const pending = treeFolderRequests.current.get(key);
    if (pending) return pending;

    const request = (async () => {
      const response = await fetch("/api/explorer/folders?parent_id=" + encodeURIComponent(id) + "&provider=" + encodeURIComponent(source));
      if (!response.ok) throw Error((await response.json()).detail);
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
    const key = provider + ":" + id;
    if (folderCache.current.has(key) || folderRequests.current.has(key)) return;
    cancelFolderPrefetch();
    prefetchTimer.current = window.setTimeout(() => {
      void fetchFolder(id, provider).catch(() => undefined);
    }, 180);
  }

  async function open(id = rootId(provider), ancestors: Asset[] = [], source: Provider = provider, preserveSelection = false) {
    const requestSequence = ++openSequence.current;
    const cached = folderCache.current.has(source + ":" + id);
    setLoading(!cached);
    setError("");
    if (!preserveSelection) setSelected(new Set());
    resetSearch();
    cancelFolderPrefetch();

    try {
      const folder = await fetchFolder(id, source);
      if (requestSequence !== openSequence.current) return;
      const nextPath = [...ancestors, folder.parent];
      setItems(folder.children);
      setPath(nextPath);
      hydrateTreePath(nextPath, source);
      cacheFolders(id, folder.children, source);
      setExpanded(current => new Set(current).add(id));
    } catch (reason) {
      if (requestSequence === openSequence.current) {
        setError(reason instanceof Error ? reason.message : "Unable to load folder");
      }
    } finally {
      if (requestSequence === openSequence.current) setLoading(false);
    }
  }

  /** Navigate from an explicit folder selection and leave search mode behind. */
  async function openFolder(id = rootId(provider), ancestors: Asset[] = [], source: Provider = provider) {
    setQuery("");
    resetSearch();
    searchV2.clearSearchFilters();
    const params = new URLSearchParams(window.location.search);
    params.delete("q");
    [...params.keys()].filter(key => key.startsWith("facet.")).forEach(key => params.delete(key));
    const cleanQuery = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (cleanQuery ? "?" + cleanQuery : ""));
    await open(id, ancestors, source);
  }

  async function restoreSavedLocation(source: Provider) {
    const saved = parseSavedExplorerLocation(window.localStorage.getItem(explorerLocationKey(source)), source);
    if (!saved) {
      window.localStorage.removeItem(explorerLocationKey(source));
      await open(rootId(source), [], source);
      return;
    }
    try {
      // A legacy saved path can begin at the selected folder. Always hydrate the
      // provider root first so the sidebar has its top-level folders to render.
      const sourceRootId = rootId(source);
      const sourceRoot = await fetchFolder(sourceRootId, source);
      const restoredPath: Asset[] = [sourceRoot.parent];
      cacheFolders(sourceRootId, sourceRoot.children, source);

      for (const item of saved.path) {
        if (item.id === sourceRootId) continue;
        const folder = await fetchFolder(item.id, source);
        if (restoredPath.at(-1)?.id !== folder.parent.id) restoredPath.push(folder.parent);
        // Populate every branch on the way down so the sidebar can render the restored route.
        cacheFolders(item.id, folder.children, source);
      }
      const current = restoredPath.at(-1);
      if (!current) throw Error("Saved folder is unavailable");
      setExpanded(new Set(restoredPath.slice(0, -1).map(folder => folder.id)));
      await open(current.id, restoredPath.slice(0, -1), source);
    } catch {
      window.localStorage.removeItem(explorerLocationKey(source));
      await open(rootId(source), [], source);
    }
  }

  async function refreshCurrentFolder() {
    const currentFolder = path.at(-1);
    if (!currentFolder) return;

    const key = provider + ":" + currentFolder.id;
    folderCache.current.delete(key);
    treeFolderCache.current.delete(key);
    await open(currentFolder.id, path.slice(0, -1), provider, true);
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

    const cached = treeFolderCache.current.get(provider + ":" + node.id);
    if (cached) {
      cacheFolders(node.id, cached, provider);
      setExpanded(current => new Set(current).add(node.id));
      return;
    }

    setLoadingTreeIds(current => new Set(current).add(node.id));
    setError("");
    try {
      const folders = await fetchTreeFolders(node.id, provider);
      cacheFolders(node.id, folders, provider);
      setExpanded(current => new Set(current).add(node.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to expand folder");
    } finally {
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
  async function deleteItem(itemId: string) { const response = await fetch("/api/explorer/items/" + encodeURIComponent(itemId) + "?provider=" + encodeURIComponent(provider), { method: "DELETE" }); if (!response.ok) throw Error("Unable to delete file"); await refreshCurrentFolder(); }
  async function moveItem(itemId: string, destinationParentId: string) { const response = await fetch("/api/explorer/items/" + encodeURIComponent(itemId) + "/move?provider=" + encodeURIComponent(provider) + "&destination_parent_id=" + encodeURIComponent(destinationParentId), { method: "POST" }); if (!response.ok) throw Error("Unable to move file"); await refreshCurrentFolder(); }
  async function copyItems(itemIds: string[], destinationParentId: string) {
    for (const itemId of itemIds) {
      const response = await fetch("/api/explorer/items/" + encodeURIComponent(itemId) + "/copy?provider=" + encodeURIComponent(provider) + "&destination_parent_id=" + encodeURIComponent(destinationParentId), { method: "POST" });
      if (!response.ok) throw Error("Unable to copy item");
    }
    await refreshCurrentFolder();
  }

  function clearExplorer(source: Provider = provider) {
    setPath([]);
    setItems([]);
    setChildrenByParent({});
    setExpanded(new Set([rootId(source)]));
    setSelected(new Set());
    setMetadataByItem({});
    setQuery("");
    resetSearch();
    setError("");
    setLoading(false);
    setLoadingTreeIds(new Set());
    folderCache.current.clear();
    folderRequests.current.clear();
    treeFolderCache.current.clear();
    treeFolderRequests.current.clear();
    openSequence.current += 1;
    cancelFolderPrefetch();
  }

  useEffect(() => {
    if (!auth.authenticated || !path.length) return;
    try { window.localStorage.setItem(explorerLocationKey(provider), JSON.stringify(savedLocation(path))); } catch { /* browser storage is optional */ }
  }, [auth.authenticated, path, provider]);

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
    async function readSession(url: string): Promise<Omit<AuthState, "checking">> {
      try { const response = await fetch(url); if (!response.ok) throw Error(); return await response.json(); }
      catch { return { authenticated: false, user: null }; }
    }
    async function initialize() {
      const [google, sharepoint] = await Promise.all([readSession("/api/auth/google/session"), readSession("/api/auth/microsoft/session")]);
      const sessions: ProviderSessions = {
        "google-drive": { ...google, checking: false },
        sharepoint: { ...sharepoint, checking: false },
      };
      const selected: Provider = preferred === "sharepoint" && sharepoint.authenticated ? "sharepoint"
        : google.authenticated ? "google-drive" : sharepoint.authenticated ? "sharepoint" : preferred;
      setAuthByProvider(sessions); setProvider(selected);
      if (sessions[selected].authenticated) await restoreSavedLocation(selected);
      else clearExplorer(selected);
    }
    void initialize();
    fetch("/api/tags").then(response => response.json()).then(setTags).catch(() => setTags([]));
    return () => { openSequence.current += 1; cancelFolderPrefetch(); };
  }, []);

  useEffect(() => {
    if (!auth.authenticated) {
      setMetadataIndex({ ...emptyIndexStatus });
      return;
    }

    let cancelled = false;
    let pollTimer: number | undefined;

    function applyStatus(status: DriveIndexStatus) {
      if (!cancelled) setMetadataIndex(status);
      return status.state;
    }

    async function poll() {
      try {
        const response = await fetch("/api/explorer/index/status?provider=" + encodeURIComponent(provider));
        if (!response.ok) throw Error("Unable to read indexing status");
        const status = await response.json() as DriveIndexStatus;
        if (applyStatus(status) === "running") {
          pollTimer = window.setTimeout(() => void poll(), 3000);
        }
      } catch (reason) {
        if (!cancelled) {
          setMetadataIndex(current => ({
            ...current,
            state: "failed",
            status: "Unable to read metadata indexing status",
            error: reason instanceof Error ? reason.message : "Index status request failed",
          }));
        }
      }
    }

    async function start() {
      setMetadataIndex({
        ...emptyIndexStatus,
        state: "running",
        status: "Starting " + (provider === "sharepoint" ? "SharePoint" : "Google Drive") + " metadata index",
        progress: 1,
      });
      try {
        const response = await fetch("/api/explorer/index/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider, root_id: rootId(provider) }),
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw Error(body.detail || "Unable to start metadata indexing");
        }
        const status = await response.json() as DriveIndexStatus;
        if (applyStatus(status) === "running") {
          pollTimer = window.setTimeout(() => void poll(), 3000);
        }
      } catch (reason) {
        if (!cancelled) {
          setMetadataIndex({
            ...emptyIndexStatus,
            state: "failed",
            status: "Metadata indexing failed",
            error: reason instanceof Error ? reason.message : "Unable to start metadata indexing",
          });
        }
      }
    }

    if (indexRetryKey > 0) void start();
    else void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(pollTimer);
    };
  }, [auth.authenticated, indexRetryKey, provider, searchV2.active, searchV2.capabilitiesResolved]);

  useEffect(() => {
    const normalizedQuery = query.trim();
    const currentFolder = path.at(-1);

    setSearchResults(null);
    setSearching(false);
    setSearchProgress(0);
    setSearchStatus("Preparing search");
    setSearchProcessedFolders(0);
    setSearchPendingFolders(0);
    setSearchError("");

    if (searchV2.active) return;

    if (
      !auth.authenticated
      || metadataIndex.state !== "completed"
      || !currentFolder
      || normalizedQuery.length < 2
    ) {
      setSearchIndexedCount(0);
      setSearchIndexSource(null);
      setSearchTruncated(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSearching(true);
      try {
        const response = await fetch("/api/explorer/search/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            provider,
            query: normalizedQuery,
            root_id: currentFolder.id,
            ancestor_ids: path.slice(0, -1).map(folder => folder.id),
            ancestor_names: path.slice(0, -1).map(folder => folder.name),
            limit: 300,
          }),
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw Error(body.detail || "Unable to search Drive metadata");
        }
        if (!response.body) throw Error("Search progress stream is unavailable");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let receivedResult = false;

        function handleEvent(event: SearchStreamEvent) {
          if (event.type === "error") {
            throw Error(event.detail || "Unable to search subfolders");
          }

          if (event.status) setSearchStatus(event.status);
          if (typeof event.progress === "number") {
            setSearchProgress(current => Math.max(current, Math.min(100, event.progress || 0)));
          }
          if (typeof event.indexed_count === "number") setSearchIndexedCount(event.indexed_count);
          if (typeof event.processed_folders === "number") {
            setSearchProcessedFolders(event.processed_folders);
          }
          if (typeof event.pending_folders === "number") {
            setSearchPendingFolders(event.pending_folders);
          }

          if (event.type === "result" && event.data) {
            receivedResult = true;
            setSearchResults(event.data.items);
            setSearchIndexedCount(event.data.indexed_count);
            setSearchIndexSource(event.data.index_source);
            setSearchTruncated(event.data.truncated);
            setSearchStatus("Search complete");
            setSearchProgress(100);
            setSearchPendingFolders(0);
          }
        }

        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          lines.filter(Boolean).forEach(line => handleEvent(JSON.parse(line) as SearchStreamEvent));
          if (done) break;
        }
        if (buffer.trim()) handleEvent(JSON.parse(buffer) as SearchStreamEvent);
        if (!receivedResult) throw Error("Search ended before results were ready");
      } catch (reason) {
        if (!controller.signal.aborted) {
          setSearchError(reason instanceof Error ? reason.message : "Unable to search subfolders");
          setSearchStatus("Search failed");
        }
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 320);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [auth.authenticated, metadataIndex.state, path, provider, query, searchV2.active]);

  async function selectProvider(source: Provider) {
    setProvider(source); setOauthError(null); clearExplorer(source);
    if (authByProvider[source].authenticated) await open(rootId(source), [], source);
  }

  async function logout() {
    await fetch(provider === "sharepoint" ? "/api/auth/microsoft/logout" : "/api/auth/google/logout", { method: "POST" });
    setAuthByProvider(current => ({ ...current, [provider]: { authenticated: false, user: null, checking: false } }));
    clearExplorer(provider);
  }

  function toggleSelection(id: string) {
    setSelected(current => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
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

  const localResults = useMemo(
    () => searchAssets(items, query),
    [items, query],
  );
  const matchedItems = useMemo(
    () => searchV2.active && query.trim().length >= 1
      ? searchV2.items
      : query.trim().length >= 2 && searchResults !== null
        ? searchAssets(searchResults, query)
        : localResults,
    [localResults, query, searchResults, searchV2.active, searchV2.items],
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
            body: JSON.stringify({ provider, item_ids: batch }),
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
  }, [auth.authenticated, provider, matchedItems]);

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
    searching: searchV2.active
      ? isSearchRequestInFlight(query, searchV2.loading)
      : searching,
    searchProgress,
    searchStatus,
    searchProcessedFolders,
    searchPendingFolders,
    searchComplete: searchV2.active ? query.trim().length >= 1 && !searchV2.loading && !searchV2.error : query.trim().length >= 2 && searchResults !== null && !searching,
    searchIndexedCount,
    searchIndexSource,
    searchTruncated,
    searchError: searchV2.active ? searchV2.error : searchError,
    searchDurationMs: searchV2.active ? searchV2.durationMs : null,
    loading,
    error,
    provider,
    auth,
    authByProvider,
    oauthError,
    metadataIndex,
    searchReady: searchV2.active || metadataIndex.state === "completed",
    searchV2,
    retryMetadataIndex: () => setIndexRetryKey(current => current + 1),
    refreshCurrentFolder,
    selectProvider,
    open,
    openFolder,
    toggleTree,
    scheduleFolderPrefetch,
    cancelFolderPrefetch,
    logout,
    toggleSelection,
    applyTag,
    rateAsset,
    applyRating,
    clearSelection: () => setSelected(new Set()),
    uploads, uploadFiles, deleteItem, moveItem, copyItems, clearUploads: () => setUploads([]), currentFolderId: path.at(-1)?.id || rootId(provider),
  };
}
