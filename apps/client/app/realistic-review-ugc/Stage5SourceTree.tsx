import { useMemo, useState } from "react";
import { RrugcActionIcon } from "./RrugcActionIcon";
import type { Stage5FolderNode } from "./stage5SourceTree";

type Props = {
  root: Stage5FolderNode;
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
  hasMore: boolean;
  totalFolders?: number;
  stage?: 3 | 4 | 5;
};

export function Stage5SourceTree({ root, selectedKey, onSelect, hasMore, totalFolders, stage = 5 }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const branches = useMemo(() => {
    const items: string[] = [];
    function walk(node: Stage5FolderNode) {
      if (node.children.length) items.push(node.key);
      node.children.forEach(walk);
    }
    root.children.forEach(walk);
    return items;
  }, [root]);
  const allExpanded = branches.length > 0 && branches.every(key => expanded.has(key));

  function toggle(key: string) {
    setExpanded(previous => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function choose(node: Stage5FolderNode) {
    onSelect(node.key);
    if (node.children.length) {
      setExpanded(previous => new Set(previous).add(node.key));
    }
  }

  function renderNode(node: Stage5FolderNode): React.ReactNode {
    const isExpanded = expanded.has(node.key);
    return <div key={node.key} className="rrugc-stage5-tree-node">
      <div className={"rrugc-stage5-tree-row" + (selectedKey === node.key ? " is-selected" : "")}
        style={{ paddingInlineStart: 6 + node.depth * 13 }}>
        {node.children.length ? (
          <button type="button" className={"rrugc-stage5-tree-toggle" + (isExpanded ? " is-expanded" : "")}
            aria-label={(isExpanded ? "Collapse " : "Expand ") + node.name}
            aria-expanded={isExpanded}
            onClick={() => toggle(node.key)}>
            <RrugcActionIcon name="chevron-right" />
          </button>
        ) : <span className="rrugc-stage5-tree-spacer" aria-hidden="true" />}
        <button type="button" className="rrugc-stage5-tree-folder"
          aria-pressed={selectedKey === node.key}
          title={node.path}
          onClick={() => choose(node)}>
          <span className="rrugc-stage5-tree-folder-icon"><RrugcActionIcon name="folder" /></span>
          <span className="rrugc-stage5-tree-folder-name">{node.name}</span>
          <span className="rrugc-stage5-tree-count">{node.imageCount}</span>
        </button>
      </div>
      {node.children.length > 0 && isExpanded &&
        <div className="rrugc-stage5-tree-children">{node.children.map(renderNode)}</div>}
    </div>;
  }

  return <div className="rrugc-stage5-source-tree" role="complementary" aria-label={`Stage ${stage} source folder tree`}>
    <header className="rrugc-stage5-tree-head">
      <div><strong>Source tree</strong><small>{stage === 3 ? "Folders from Drive sources" : stage === 4 ? "Folders from Stage 3 sources" : "Folders from Stage 4 outputs"}</small></div>
      <button type="button" className="rrugc-stage5-tree-expand"
        disabled={!branches.length}
        onClick={() => setExpanded(allExpanded ? new Set() : new Set(branches))}>
        {allExpanded ? "Collapse" : "Expand all"}
      </button>
    </header>
    <div className="rrugc-stage5-tree-scroll" role="navigation" aria-label="Choose a source folder">
      <button type="button"
        className={"rrugc-stage5-tree-all" + (selectedKey === null ? " is-selected" : "")}
        aria-pressed={selectedKey === null}
        onClick={() => onSelect(null)}>
        <RrugcActionIcon name="select-all" /><strong>All folders</strong>
        <span className="rrugc-stage5-tree-count">{root.imageCount}</span>
      </button>
      {root.children.map(renderNode)}
      {!root.children.length && <p className="rrugc-stage5-tree-empty">No folders found.</p>}
    </div>
    <footer className="rrugc-stage5-tree-foot">
      <span>{totalFolders ?? root.groupIds.length} folders · {root.imageCount} images</span>
      {hasMore && <small>Folder totals include unloaded images; select a folder and load more to see every image.</small>}
    </footer>
  </div>;
}
