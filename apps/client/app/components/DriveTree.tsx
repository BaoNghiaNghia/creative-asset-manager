import type { Asset, TreeCache } from "../types";
import { ChevronIcon, FolderTreeIcon } from "./Icons";

type Props = {
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

export function DriveTreeNode({
  node,
  ancestors,
  activeId,
  childrenByParent,
  expanded,
  loadingNodes,
  onOpen,
  onToggle,
  onPrefetch,
  onCancelPrefetch,
}: Props) {
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
      {children.map(child => <DriveTreeNode
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
