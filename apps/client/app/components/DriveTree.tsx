import type { Asset, TreeCache } from "../types";
import { FolderReviewLinkActions, reviewShareIdForFolder } from "./FolderReviewLinkActions";
import { ChevronIcon, SourceFolderIcon } from "./Icons";

export function TreeChildrenSkeleton({ rows = 3 }: { rows?: number }) {
  return <div className="tree-children tree-children-skeleton" aria-label="Loading folders" aria-busy="true">
    {Array.from({ length: rows }, (_, index) => <div className="tree-skeleton-row" key={index}>
      <i aria-hidden="true" /><span aria-hidden="true" />
    </div>)}
  </div>;
}

type Props = {
  node: Asset;
  ancestors: Asset[];
  activeId?: string;
  activePathIds: Set<string>;
  childrenByParent: TreeCache;
  expanded: Set<string>;
  loadingNodes: Set<string>;
  onOpen: (id: string, ancestors: Asset[]) => void;
  onToggle: (node: Asset) => void;
  onPrefetch: (id: string) => void;
  onCancelPrefetch: () => void;
  reviewLinkShareIds?: ReadonlyMap<string, string>;
  activeExternalSourceId?: string | null;
  onCopyReviewLink?: (shareId: string, item: Asset) => void | Promise<void>;
  onRefreshReviewLink?: (shareId: string, item: Asset) => void | Promise<void>;
};

export function DriveTreeNode({
  node,
  ancestors,
  activeId,
  activePathIds,
  childrenByParent,
  expanded,
  loadingNodes,
  onOpen,
  onToggle,
  onPrefetch,
  onCancelPrefetch,
  reviewLinkShareIds,
  activeExternalSourceId,
  onCopyReviewLink,
  onRefreshReviewLink,
}: Props) {
  const isExpanded = expanded.has(node.id);
  const isLoading = loadingNodes.has(node.id);
  const children = childrenByParent[node.id] ?? [];
  const childrenLoaded = Object.prototype.hasOwnProperty.call(childrenByParent, node.id);
  const canExpand = !childrenLoaded || children.length > 0;
  const isCurrent = activeId === node.id;
  const isAncestor = !isCurrent && activePathIds.has(node.id);
  const rowState = isCurrent ? "active" : isAncestor ? "active-path" : "";
  const reviewShareId = reviewLinkShareIds
    ? reviewShareIdForFolder(node, activeExternalSourceId, reviewLinkShareIds)
    : null;

  return <div className="tree-node">
    <div
      className={"tree-row " + rowState}
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
        <SourceFolderIcon name={node.name} />
        <span>{node.name}</span>
      </button>
      {reviewShareId && onCopyReviewLink && onRefreshReviewLink && <FolderReviewLinkActions
        item={node}
        shareId={reviewShareId}
        onCopyReviewLink={onCopyReviewLink}
        onRefreshReviewLink={onRefreshReviewLink}
      />}
    </div>
    {isExpanded && (isLoading
      ? <TreeChildrenSkeleton />
      : children.length > 0 && <div className="tree-children">
        {children.map(child => <DriveTreeNode
          key={child.id}
          node={child}
          ancestors={[...ancestors, node]}
          activeId={activeId}
          activePathIds={activePathIds}
          childrenByParent={childrenByParent}
          expanded={expanded}
          loadingNodes={loadingNodes}
          onOpen={onOpen}
          onToggle={onToggle}
          onPrefetch={onPrefetch}
          onCancelPrefetch={onCancelPrefetch}
          reviewLinkShareIds={reviewLinkShareIds}
          activeExternalSourceId={activeExternalSourceId}
          onCopyReviewLink={onCopyReviewLink}
          onRefreshReviewLink={onRefreshReviewLink}
        />)}
      </div>
    )}
  </div>;
}
