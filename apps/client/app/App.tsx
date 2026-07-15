import { useEffect, useMemo, useState } from "react";

type Asset = {
  id: string;
  name: string;
  kind: "folder" | "image" | "video" | "pdf" | "document" | "other";
  mime_type: string;
  modified_at?: string;
};

type Tag = { id: string; name: string; color: string };
type GoogleUser = { id: string; name?: string; email?: string; picture?: string };
type AuthState = { authenticated: boolean; user: GoogleUser | null };
type Folder = { parent: Asset; children: Asset[] };
type TreeCache = Record<string, Asset[]>;

const icons = { folder: "📁", image: "▧", video: "▶", pdf: "PDF", document: "DOC", other: "◇" };

type TreeNodeProps = {
  node: Asset;
  ancestors: Asset[];
  activeId?: string;
  childrenByParent: TreeCache;
  expanded: Set<string>;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (node: Asset) => void;
};

function TreeNode({ node, ancestors, activeId, childrenByParent, expanded, onOpen, onToggle }: TreeNodeProps) {
  const isExpanded = expanded.has(node.id);
  const children = childrenByParent[node.id] ?? [];

  return <div className="tree-node">
    <div className={"tree-row " + (activeId === node.id ? "active" : "")}>
      <button
        className="tree-toggle"
        onClick={() => onToggle(node)}
        aria-label={(isExpanded ? "Collapse " : "Expand ") + node.name}
      >
        {isExpanded ? "▾" : "▸"}
      </button>
      <button className="tree-label" onClick={() => onOpen(node.id, ancestors)}>
        {node.name}
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
        onOpen={onOpen}
        onToggle={onToggle}
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
  const [auth, setAuth] = useState<AuthState>({ authenticated: false, user: null });

  async function fetchFolder(id: string): Promise<Folder> {
    const response = await fetch("/api/explorer/children?parent_id=" + encodeURIComponent(id));
    if (!response.ok) throw Error((await response.json()).detail);
    return response.json();
  }

  function cacheFolders(id: string, children: Asset[]) {
    setChildrenByParent(current => ({
      ...current,
      [id]: children.filter(item => item.kind === "folder"),
    }));
  }

  async function open(id = "root", ancestors: Asset[] = []) {
    setLoading(true);
    setError("");
    setSelected(new Set());

    try {
      const folder = await fetchFolder(id);
      setItems(folder.children);
      setPath([...ancestors, folder.parent]);
      cacheFolders(id, folder.children);
      setExpanded(current => new Set(current).add(id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load folder");
    } finally {
      setLoading(false);
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

    try {
      if (!childrenByParent[node.id]) {
        const folder = await fetchFolder(node.id);
        cacheFolders(node.id, folder.children);
      }
      setExpanded(current => new Set(current).add(node.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to expand folder");
    }
  }

  useEffect(() => {
    fetch("/api/auth/google/session")
      .then(response => response.json())
      .then(setAuth)
      .catch(() => setAuth({ authenticated: false, user: null }));
    open();
    fetch("/api/tags").then(response => response.json()).then(setTags).catch(() => setTags([]));
  }, []);

  async function logout() {
    await fetch("/api/auth/google/logout", { method: "POST" });
    setAuth({ authenticated: false, user: null });
    await open();
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

  return <main className="shell">
    <aside>
      <div className="brand"><b>C</b><span><strong>Creative assets</strong><small>{auth.user?.email || "Google Drive"}</small></span></div>
      <p>SOURCES</p>
      <button className={"source " + (activeId === "root" ? "active" : "")} onClick={() => open("root")}>◆ My Drive</button>
      {!auth.authenticated && <button className="google-login" onClick={() => window.location.assign("/api/auth/google/login")}>
        <span>G</span> Sign in with Google
      </button>}
      <div className="tree">
        {rootFolders.map(folder => <TreeNode
          key={folder.id}
          node={folder}
          ancestors={path.length > 0 && path[0].id === "root" ? [path[0]] : []}
          activeId={activeId}
          childrenByParent={childrenByParent}
          expanded={expanded}
          onOpen={open}
          onToggle={toggleTree}
        />)}
      </div>
      <p>TAGS</p>
      {tags.map(tag => <button className="tag" key={tag.id}><i style={{ background: tag.color }} />{tag.name}</button>)}
      <div className="storage">Storage <b>42 GB / 100 GB</b><span><i /></span></div>
    </aside>

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
        <button className="upload">＋ Upload</button>
      </nav>
      {error && <div className="error">{error}</div>}
      <div className="title"><span><h1>{path.at(-1)?.name || "My Drive"}</h1><small>{items.length} items</small></span><b>▦　☷</b></div>
      {loading ? <div className="state">Loading assets…</div> : <div className="grid">
        {visible.map(item => <article className={selected.has(item.id) ? "selected" : ""} key={item.id}>
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
    </section>
  </main>;
}
