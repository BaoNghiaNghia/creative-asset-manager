import { useState } from "react";
import { api, type Child, type Folder } from "./api";

const keyOf = (folder: Folder) => folder.source_id + ":" + folder.folder_id;

type Props = {
  shareId: string;
  roots: Folder[];
  active?: Folder;
  onOpen: (folder: Folder, trail: Folder[]) => void;
};

export function PublicSourceTree({ shareId, roots, active, onOpen }: Props) {
  const [children, setChildren] = useState<Record<string, Child[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggle = async (folder: Folder) => {
    const key = keyOf(folder);
    if (expanded[key]) {
      setExpanded(previous => ({ ...previous, [key]: false }));
      return;
    }
    try {
      const items = children[key] || (await api.children(shareId, folder)).items;
      setChildren(previous => ({ ...previous, [key]: items }));
      setExpanded(previous => ({ ...previous, [key]: true }));
    } catch {
      // The parent route retains the generic public-share denial behavior.
    }
  };

  const Node = ({ folder, trail }: { folder: Folder; trail: Folder[] }) => {
    const key = keyOf(folder);
    const isExpanded = Boolean(expanded[key]);
    const folders = (children[key] || []).filter((item): item is Folder & { kind: "folder" } => item.kind === "folder");
    return <li>
      <div className={"public-tree-row" + (active && keyOf(active) === key ? " active" : "")}>
        <button className="public-tree-toggle" aria-label={(isExpanded ? "Collapse " : "Expand ") + folder.name} onClick={() => void toggle(folder)}>{isExpanded ? "⌄" : "›"}</button>
        <button className="public-tree-label" onClick={() => onOpen(folder, trail)}><span className="public-folder-icon" aria-hidden="true" /><span>{folder.name}</span></button>
      </div>
      {isExpanded && folders.length > 0 && <ol className="public-tree-children">{folders.map(child => <Node key={keyOf(child)} folder={child} trail={[...trail, child]} />)}</ol>}
    </li>;
  };

  return <aside className="public-source-tree" aria-label="Shared folder tree">
    <h2>Source tree</h2>
    <ol className="public-tree-roots">{roots.map(folder => <Node key={keyOf(folder)} folder={folder} trail={[folder]} />)}</ol>
  </aside>;
}
