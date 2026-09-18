import { useEffect, useState } from "react";
import { ChevronIcon, SourceFolderIcon } from "../components/Icons";
import { api, type Child, type Folder } from "./api";

const keyOf = (folder: Folder) => folder.source_id + ":" + folder.folder_id;

type Props = {
  shareId: string;
  roots: Folder[];
  active?: Folder;
  activeTrail: Folder[];
  onOpen: (folder: Folder, trail: Folder[]) => void;
};

export function PublicTreeSkeleton({ count = 4 }: { count?: number }) {
  return <div className="tree-children tree-children-skeleton" aria-label="Loading folders" aria-busy="true">
    {Array.from({ length: count }, (_, index) => <div className="tree-skeleton-row" key={index}><i aria-hidden="true" /><span aria-hidden="true" /></div>)}
  </div>;
}

export function PublicSourceTree({ shareId, roots, active, activeTrail, onOpen }: Props) {
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
      // Retain generic public-share denial and do not reveal folder state.
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
    const childFolders = (children[key] || []).filter((item): item is Folder & { kind: "folder" } => item.kind === "folder");
    const childrenLoaded = Object.prototype.hasOwnProperty.call(children, key);
    const canExpand = !childrenLoaded || childFolders.length > 0;
    const isCurrent = Boolean(active && keyOf(active) === key);
    const isAncestor = !isCurrent && activeTrail.some(item => keyOf(item) === key);
    const rowState = isCurrent ? "active" : isAncestor ? "active-path" : "";

    return <div className="tree-node">
      <div className={"tree-row " + rowState}>
        {canExpand ? <button className={"tree-toggle " + (isLoading ? "loading" : "")} onClick={() => void toggle(folder)} aria-label={(isExpanded ? "Collapse " : "Expand ") + folder.name} disabled={isLoading}>{isLoading ? <span className="tree-loading" /> : <ChevronIcon expanded={isExpanded} />}</button> : <span className="tree-toggle-placeholder" aria-hidden="true" />}
        <button className="tree-label" title={folder.name} onClick={() => onOpen(folder, trail)}><SourceFolderIcon name={folder.name} /><span>{folder.name}</span></button>
      </div>
      {isExpanded && (isLoading ? <PublicTreeSkeleton /> : childFolders.length > 0 && <div className="tree-children">{childFolders.map(child => <Node key={keyOf(child)} folder={child} trail={[...trail, child]} />)}</div>)}
    </div>;
  };

  return <aside className="public-source-tree" aria-label="Shared folder tree">
    <h2>Source tree</h2>
    {roots.map(folder => <Node key={keyOf(folder)} folder={folder} trail={[folder]} />)}
  </aside>;
}
