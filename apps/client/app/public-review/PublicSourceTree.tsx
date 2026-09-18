import { useEffect, useState } from "react";
import { api, type Child, type Folder } from "./api";

const keyOf = (folder: Folder) => folder.source_id + ":" + folder.folder_id;

type Props = {
  shareId: string;
  roots: Folder[];
  active?: Folder;
  onOpen: (folder: Folder, trail: Folder[]) => void;
};

export function PublicTreeSkeleton({ count = 4 }: { count?: number }) {
  return <ol className="public-tree-skeleton" aria-label="Loading folders">
    {Array.from({ length: count }, (_, index) => <li className="public-tree-skeleton-row" key={index} aria-hidden="true"><i /><span /></li>)}
  </ol>;
}

export function PublicSourceTree({ shareId, roots, active, onOpen }: Props) {
  const [children, setChildren] = useState<Record<string, Child[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});

  const loadFolder = async (folder: Folder) => {
    const key = keyOf(folder);
    setExpanded(previous => ({ ...previous, [key]: true }));
    setLoading(previous => ({ ...previous, [key]: true }));
    try {
      const first = await api.children(shareId, folder);
      const items = [...first.items];
      let nextOffset = first.next_offset;
      while (nextOffset !== null) {
        const page = await api.children(shareId, folder, nextOffset);
        items.push(...page.items);
        nextOffset = page.next_offset;
      }
      setChildren(previous => ({ ...previous, [key]: items }));
    } catch {
      // Keep the generic public-share denial behavior.
    } finally {
      setLoading(previous => ({ ...previous, [key]: false }));
    }
  };

  useEffect(() => {
    setChildren({});
    setExpanded({});
    setLoading({});
    roots.forEach(folder => { void loadFolder(folder); });
  }, [shareId, roots]);

  const toggle = async (folder: Folder) => {
    const key = keyOf(folder);
    if (expanded[key]) {
      setExpanded(previous => ({ ...previous, [key]: false }));
      return;
    }
    if (children[key]) {
      setExpanded(previous => ({ ...previous, [key]: true }));
      return;
    }
    await loadFolder(folder);
  };

  const Node = ({ folder, trail }: { folder: Folder; trail: Folder[] }) => {
    const key = keyOf(folder);
    const isExpanded = Boolean(expanded[key]);
    const isLoading = Boolean(loading[key]);
    const folders = (children[key] || []).filter((item): item is Folder & { kind: "folder" } => item.kind === "folder");
    return <li>
      <div className={"public-tree-row" + (active && keyOf(active) === key ? " active" : "") + (folder.name.startsWith("Amazon") ? " amazon" : folder.name.startsWith("Etsy") ? " etsy" : "")}>
        <button className="public-tree-toggle" aria-label={(isExpanded ? "Collapse " : "Expand ") + folder.name} aria-busy={isLoading || undefined} onClick={() => void toggle(folder)}>{isLoading ? <span className="public-tree-spinner" aria-hidden="true" /> : isExpanded ? "⌄" : "›"}</button>
        <button className="public-tree-label" onClick={() => onOpen(folder, trail)}><span className="public-folder-icon" aria-hidden="true" /><span>{folder.name}</span></button>
      </div>
      {isExpanded && isLoading && <PublicTreeSkeleton />}
      {isExpanded && !isLoading && folders.length > 0 && <ol className="public-tree-children">{folders.map(child => <Node key={keyOf(child)} folder={child} trail={[...trail, child]} />)}</ol>}
    </li>;
  };

  return <aside className="public-source-tree" aria-label="Shared folder tree">
    <h2>Source tree</h2>
    <ol className="public-tree-roots">{roots.map(folder => <Node key={keyOf(folder)} folder={folder} trail={[folder]} />)}</ol>
  </aside>;
}
