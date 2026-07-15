import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

type Asset = {
  id: string;
  name: string;
  kind: "folder" | "image" | "video" | "pdf" | "document" | "other";
  mime_type: string;
  modified_at?: string;
};

type Tag = { id: string; name: string; color: string };
type GoogleUser = { id: string; name?: string; email?: string; picture?: string };
type AuthState = { authenticated: boolean; user: GoogleUser | null; checking: boolean };
type OAuthErrorState = { message: string; requestId?: string } | null;
type Folder = { parent: Asset; children: Asset[] };
type TreeCache = Record<string, Asset[]>;

const icons = { folder: "📁", image: "▧", video: "▶", pdf: "PDF", document: "DOC", other: "◇" };
const MIN_SIDEBAR_WIDTH = 220;
const MAX_SIDEBAR_WIDTH = 480;
const DEFAULT_SIDEBAR_WIDTH = 256;

function ChevronIcon({ expanded = false }: { expanded?: boolean }) {
  return <svg className={"chevron-icon " + (expanded ? "expanded" : "")} viewBox="0 0 16 16" aria-hidden="true">
    <path d="m6 3.5 4.5 4.5L6 12.5" />
  </svg>;
}

function DriveIcon() {
  return <svg className="drive-icon" viewBox="0 0 20 20" aria-hidden="true">
    <path d="M6.2 2.5h5.1l5.9 10.2-2.6 4.6H9.4l2.6-4.6h5.2" />
    <path d="m6.2 2.5-5.8 10.2L3 17.3h6.4L12 12.7 6.2 2.5Z" />
  </svg>;
}

function FolderTreeIcon() {
  return <svg className="folder-tree-icon" viewBox="0 0 18 18" aria-hidden="true">
    <path d="M2.5 5.25h5l1.3 1.5h6.7v7.75h-13Z" />
    <path d="M2.5 5.25V3.5h4.1l1.3 1.75" />
  </svg>;
}

function SidebarIcon({ open }: { open: boolean }) {
  return <svg viewBox="0 0 18 18" aria-hidden="true">
    <rect x="2.25" y="3" width="13.5" height="12" rx="1.5" />
    <path d="M6.25 3v12" />
    <path d={open ? "m11 6-3 3 3 3" : "m9 6 3 3-3 3"} />
  </svg>;
}

type TreeNodeProps = {
  node: Asset;
  ancestors: Asset[];
  activeId?: string;
  childrenByParent: TreeCache;
  expanded: Set<string>;
  loadingNodes: Set<string>;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (node: Asset) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
};

function TreeNode({ node, ancestors, activeId, childrenByParent, expanded, loadingNodes, onOpen, onToggle, onPrefetch, onCancelPrefetch }: TreeNodeProps) {
  const isExpanded = expanded.has(node.id);
  const isLoading = loadingNodes.has(node.id);
  const children = childrenByParent[node.id] ?? [];
  const childrenLoaded = Object.prototype.hasOwnProperty.call(childrenByParent, node.id);
  const canExpand = !childrenLoaded || children.length > 0;

  return <div className="tree-node">
    <div
      className={"tree-row " + (activeId === node.id ? "active" : "")}
      onPointerEnter={() => onPrefetch(node.id)}
      onPointerLeave={onCancelPrefetch}
    >
      {canExpand ? <button
        className={"tree-toggle " + (isLoading ? "loading" : "")}
        onClick={() => onToggle(node)}
        aria-label={(isExpanded ? "Collapse " : "Expand ") + node.name}
        disabled={isLoading}
      >
        {isLoading ? <span className="tree-loading" /> : <ChevronIcon expanded={isExpanded} />}
      </button> : <span className="tree-toggle-placeholder" aria-hidden="true" />}
      <button className="tree-label" title={node.name} onClick={() => onOpen(node.id, ancestors)}>
        <FolderTreeIcon />
        <span>{node.name}</span>
      </button>
    </div>
    {isExpanded && children.length > 0 && <div className="tree-children">
      {children.map(child => <TreeNode
        key={child.id}
        node={child}
        ancestors={[...ancestors, node]}
        activeId={activeId}
        childrenByParent={childrenByParent}
        expanded={expanded}
        loadingNodes={loadingNodes}
        onOpen={onOpen}
        onToggle={onToggle}
        onPrefetch={onPrefetch}
        onCancelPrefetch={onCancelPrefetch}
      />)}
    </div>}
  </div>;
}

export default function App() {
  const [path, setPath] = useState<Asset[]>([]);
  const [items, setItems] = useState<Asset[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [childrenByParent, setChildrenByParent] = useState<TreeCache>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [auth, setAuth] = useState<AuthState>({ authenticated: false, user: null, checking: true });
  const [oauthError, setOauthError] = useState<OAuthErrorState>(null);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem("cam-sidebar-width"));
    return Number.isFinite(saved) && saved >= MIN_SIDEBAR_WIDTH && saved <= MAX_SIDEBAR_WIDTH
      ? saved
      : DEFAULT_SIDEBAR_WIDTH;
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem("cam-sidebar-collapsed") === "true",
  );
  const resizingSidebar = useRef(false);
  const folderCache = useRef(new Map<string, Folder>());
  const folderRequests = useRef(new Map<string, Promise<Folder>>());
  const treeFolderCache = useRef(new Map<string, Asset[]>());
  const treeFolderRequests = useRef(new Map<string, Promise<Asset[]>>());
  const prefetchTimer = useRef<number | undefined>(undefined);
  const openSequence = useRef(0);
  const [loadingTreeIds, setLoadingTreeIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    function resizeSidebar(event: PointerEvent) {
      if (!resizingSidebar.current) return;
      const nextWidth = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, event.clientX));
      setSidebarWidth(nextWidth);
      window.localStorage.setItem("cam-sidebar-width", String(nextWidth));
    }

    function stopResizingSidebar() {
      if (!resizingSidebar.current) return;
      resizingSidebar.current = false;
      document.body.classList.remove("resizing-sidebar");
    }

    window.addEventListener("pointermove", resizeSidebar);
    window.addEventListener("pointerup", stopResizingSidebar);
    return () => {
      window.removeEventListener("pointermove", resizeSidebar);
      window.removeEventListener("pointerup", stopResizingSidebar);
    };
  }, []);

  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    resizingSidebar.current = true;
    document.body.classList.add("resizing-sidebar");
  }

  function setSidebarVisibility(collapsed: boolean) {
    setSidebarCollapsed(collapsed);
    window.localStorage.setItem("cam-sidebar-collapsed", String(collapsed));
  }

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
    const messages: Record<string, string> = {
      denied: "Google access was cancelled or denied.",
      incomplete: "Google returned an incomplete authorization response.",
      state: "The sign-in request expired. Please start again.",
      token_exchange: "Google could not complete the secure token exchange.",
      scope: "The required Google Drive read-only permission was not granted.",
      profile: "Google connected, but the account profile could not be loaded.",
    };
    if (errorCode) {
      setOauthError({
        message: messages[errorCode] || "Google sign-in could not be completed.",
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

    initialize();
    fetch("/api/tags").then(response => response.json()).then(setTags).catch(() => setTags([]));
  }, []);

  async function logout() {
    await fetch("/api/auth/google/logout", { method: "POST" });
    setAuth({ authenticated: false, user: null, checking: false });
    clearExplorer();
  }

  const visible = useMemo(
    () => items.filter(item => item.name.toLowerCase().includes(query.toLowerCase())),
    [items, query],
  );

  function toggle(id: string) {
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

  const activeId = path.at(-1)?.id;
  const rootFolders = childrenByParent.root ?? [];

  return <main
    className={"shell " + (sidebarCollapsed ? "sidebar-collapsed" : "")}
    style={{ "--sidebar-width": sidebarWidth + "px" } as CSSProperties}
  >
    <aside className="sidebar">
      <button className="sidebar-collapse" onClick={() => setSidebarVisibility(true)} aria-label="Collapse sidebar" title="Collapse sidebar">
        <SidebarIcon open />
      </button>
      <div className="brand"><b>C</b><span><strong>Creative assets</strong><small>{auth.user?.email || "Google Drive"}</small></span></div>
      <p>SOURCES</p>
      {auth.checking ? <div className="source-skeleton"><i /><i /><i /></div> : auth.authenticated ? <>
        <button className={"source " + (activeId === "root" ? "active" : "")} onClick={() => open("root")}><DriveIcon /><span>My Drive</span></button>
        <div className="tree">
          {rootFolders.map(folder => <TreeNode
            key={folder.id}
            node={folder}
            ancestors={path.length > 0 && path[0].id === "root" ? [path[0]] : []}
            activeId={activeId}
            childrenByParent={childrenByParent}
            expanded={expanded}
            loadingNodes={loadingTreeIds}
            onOpen={open}
            onToggle={toggleTree}
            onPrefetch={scheduleFolderPrefetch}
            onCancelPrefetch={cancelFolderPrefetch}
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
      <div className="sidebar-resizer" onPointerDown={startSidebarResize} role="separator" aria-label="Resize sidebar" aria-orientation="vertical" />
    </aside>
    {sidebarCollapsed && <button className="sidebar-restore" onClick={() => setSidebarVisibility(false)} aria-label="Open sidebar" title="Open sidebar">
      <SidebarIcon open={false} />
    </button>}

    <section>
      <header>
        <label>⌕<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search this folder" /></label>
        {auth.authenticated ? <div className="account">
          {auth.user?.picture ? <img className="avatar" src={auth.user.picture} alt="" referrerPolicy="no-referrer" /> : <div className="avatar">{auth.user?.name?.slice(0, 2) || "G"}</div>}
          <button onClick={logout}>Sign out</button>
        </div> : <button className="header-login" onClick={() => window.location.assign("/api/auth/google/login")}>Sign in with Google</button>}
      </header>
      <nav>
        <div>{path.map((folder, index) => <button key={folder.id} onClick={() => open(folder.id, path.slice(0, index))}>{folder.name}</button>)}</div>
        {auth.authenticated && <button className="upload">＋ Upload</button>}
      </nav>
      {auth.checking ? <div className="state">Checking Google connection…</div> : !auth.authenticated ? <div className="drive-empty">
        {oauthError && <div className="oauth-error">
          <strong>Google sign-in failed</strong>
          <span>{oauthError.message}</span>
          {oauthError.requestId && <small>Request ID: {oauthError.requestId}</small>}
        </div>}
        <span className="drive-empty-icon">◆</span>
        <h1>Connect your Google Drive</h1>
        <p>Sign in with Google to browse your complete folder tree and files.</p>
        <button onClick={() => window.location.assign("/api/auth/google/login")}>Sign in with Google</button>
      </div> : <>
        {error && <div className="error">{error}</div>}
        <div className="title"><span><h1>{path.at(-1)?.name || "My Drive"}</h1><small>{items.length} items</small></span><b>▦　☷</b></div>
        {loading ? <div className="state">Loading assets…</div> : <div className="grid">
          {visible.map(item => <article
            className={selected.has(item.id) ? "selected" : ""}
            key={item.id}
            onPointerEnter={() => item.kind === "folder" && scheduleFolderPrefetch(item.id)}
            onPointerLeave={cancelFolderPrefetch}
          >
            <button className="check" onClick={() => toggle(item.id)}>{selected.has(item.id) ? "✓" : ""}</button>
            <button className={"preview " + item.kind} onDoubleClick={() => item.kind === "folder" && open(item.id, path)}><span>{icons[item.kind]}</span></button>
            <div>
              <button className="name" onDoubleClick={() => item.kind === "folder" && open(item.id, path)}>{item.name}</button>
              <small>{item.kind === "folder" ? "Folder" : item.modified_at ? new Date(item.modified_at).toLocaleDateString() : item.mime_type}</small>
            </div>
          </article>)}
        </div>}
        {!loading && !visible.length && <div className="state">No assets found</div>}
        {selected.size > 0 && <div className="bulk"><b>{selected.size} selected</b>{tags.map(tag => <button key={tag.id} onClick={() => applyTag(tag.id)}><i style={{ background: tag.color }} />{tag.name}</button>)}<button onClick={() => setSelected(new Set())}>×</button></div>}
      </>}
    </section>
  </main>;
}
