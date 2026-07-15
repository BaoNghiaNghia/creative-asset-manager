import { useEffect, useMemo, useRef, useState } from "react";
import type { Asset, AuthState, Folder, OAuthErrorState, Tag, TreeCache } from "../types";
import { searchAssets } from "../utils/searchAssets";

const oauthMessages: Record<string, string> = {
  denied: "Google access was cancelled or denied.",
  incomplete: "Google returned an incomplete authorization response.",
  state: "The sign-in request expired. Please start again.",
  token_exchange: "Google could not complete the secure token exchange.",
  scope: "The required Google Drive read-only permission was not granted.",
  profile: "Google connected, but the account profile could not be loaded.",
};

export function useDriveExplorer() {
  const [path, setPath] = useState<Asset[]>([]);
  const [items, setItems] = useState<Asset[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [childrenByParent, setChildrenByParent] = useState<TreeCache>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [loadingTreeIds, setLoadingTreeIds] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [auth, setAuth] = useState<AuthState>({ authenticated: false, user: null, checking: true });
  const [oauthError, setOauthError] = useState<OAuthErrorState>(null);

  const folderCache = useRef(new Map<string, Folder>());
  const folderRequests = useRef(new Map<string, Promise<Folder>>());
  const treeFolderCache = useRef(new Map<string, Asset[]>());
  const treeFolderRequests = useRef(new Map<string, Promise<Asset[]>>());
  const prefetchTimer = useRef<number | undefined>(undefined);
  const openSequence = useRef(0);

  async function fetchFolder(id: string): Promise<Folder> {
    const cached = folderCache.current.get(id);
    if (cached) return cached;

    const pending = folderRequests.current.get(id);
    if (pending) return pending;

    const request = (async () => {
      const response = await fetch("/api/explorer/children?parent_id=" + encodeURIComponent(id));
      if (!response.ok) throw Error((await response.json()).detail);
      const folder = await response.json() as Folder;
      folderCache.current.set(id, folder);
      return folder;
    })();

    folderRequests.current.set(id, request);
    try {
      return await request;
    } finally {
      folderRequests.current.delete(id);
    }
  }

  function cacheFolders(id: string, children: Asset[]) {
    const folders = children.filter(item => item.kind === "folder");
    treeFolderCache.current.set(id, folders);
    setChildrenByParent(current => ({ ...current, [id]: folders }));
  }

  async function fetchTreeFolders(id: string): Promise<Asset[]> {
    const cached = treeFolderCache.current.get(id);
    if (cached) return cached;

    const fullFolder = folderCache.current.get(id);
    if (fullFolder) return fullFolder.children.filter(item => item.kind === "folder");

    const fullRequest = folderRequests.current.get(id);
    if (fullRequest) {
      const folder = await fullRequest;
      return folder.children.filter(item => item.kind === "folder");
    }

    const pending = treeFolderRequests.current.get(id);
    if (pending) return pending;

    const request = (async () => {
      const response = await fetch("/api/explorer/folders?parent_id=" + encodeURIComponent(id));
      if (!response.ok) throw Error((await response.json()).detail);
      const folders = await response.json() as Asset[];
      treeFolderCache.current.set(id, folders);
      return folders;
    })();

    treeFolderRequests.current.set(id, request);
    try {
      return await request;
    } finally {
      treeFolderRequests.current.delete(id);
    }
  }

  function cancelFolderPrefetch() {
    window.clearTimeout(prefetchTimer.current);
    prefetchTimer.current = undefined;
  }

  function scheduleFolderPrefetch(id: string) {
    if (folderCache.current.has(id) || folderRequests.current.has(id)) return;
    cancelFolderPrefetch();
    prefetchTimer.current = window.setTimeout(() => {
      void fetchFolder(id).catch(() => undefined);
    }, 180);
  }

  async function open(id = "root", ancestors: Asset[] = []) {
    const requestSequence = ++openSequence.current;
    const cached = folderCache.current.has(id);
    setLoading(!cached);
    setError("");
    setSelected(new Set());
    cancelFolderPrefetch();

    try {
      const folder = await fetchFolder(id);
      if (requestSequence !== openSequence.current) return;
      setItems(folder.children);
      setPath([...ancestors, folder.parent]);
      cacheFolders(id, folder.children);
      setExpanded(current => new Set(current).add(id));
    } catch (reason) {
      if (requestSequence === openSequence.current) {
        setError(reason instanceof Error ? reason.message : "Unable to load folder");
      }
    } finally {
      if (requestSequence === openSequence.current) setLoading(false);
    }
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

    const cached = treeFolderCache.current.get(node.id);
    if (cached) {
      cacheFolders(node.id, cached);
      setExpanded(current => new Set(current).add(node.id));
      return;
    }

    setLoadingTreeIds(current => new Set(current).add(node.id));
    setError("");
    try {
      const folders = await fetchTreeFolders(node.id);
      cacheFolders(node.id, folders);
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

  function clearExplorer() {
    setPath([]);
    setItems([]);
    setChildrenByParent({});
    setExpanded(new Set(["root"]));
    setSelected(new Set());
    setQuery("");
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
    const params = new URLSearchParams(window.location.search);
    const errorCode = params.get("auth_error");
    const requestId = params.get("auth_request") || undefined;

    if (errorCode) {
      setOauthError({
        message: oauthMessages[errorCode] || "Google sign-in could not be completed.",
        requestId,
      });
    }

    if (errorCode || params.has("google")) {
      params.delete("auth_error");
      params.delete("auth_request");
      params.delete("auth_message");
      params.delete("google");
      const cleanQuery = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (cleanQuery ? "?" + cleanQuery : ""));
    }

    async function initialize() {
      try {
        const response = await fetch("/api/auth/google/session");
        const session = await response.json() as Omit<AuthState, "checking">;
        setAuth({ ...session, checking: false });
        if (session.authenticated) await open();
        else clearExplorer();
      } catch {
        setAuth({ authenticated: false, user: null, checking: false });
        clearExplorer();
      }
    }

    void initialize();
    fetch("/api/tags").then(response => response.json()).then(setTags).catch(() => setTags([]));

    return () => {
      openSequence.current += 1;
      cancelFolderPrefetch();
    };
  }, []);

  async function logout() {
    await fetch("/api/auth/google/logout", { method: "POST" });
    setAuth({ authenticated: false, user: null, checking: false });
    clearExplorer();
  }

  function toggleSelection(id: string) {
    setSelected(current => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function applyTag(tagId: string) {
    const response = await fetch("/api/tags/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_ids: [...selected], tag_id: tagId }),
    });
    response.ok ? setSelected(new Set()) : setError("Unable to assign tag");
  }

  const visibleItems = useMemo(
    () => searchAssets(items, query),
    [items, query],
  );

  return {
    path,
    items,
    visibleItems,
    tags,
    selected,
    childrenByParent,
    expanded,
    loadingTreeIds,
    query,
    setQuery,
    loading,
    error,
    auth,
    oauthError,
    open,
    toggleTree,
    scheduleFolderPrefetch,
    cancelFolderPrefetch,
    logout,
    toggleSelection,
    applyTag,
    clearSelection: () => setSelected(new Set()),
  };
}
