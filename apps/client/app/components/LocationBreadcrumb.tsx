import type { Asset } from "../types";

export type LocationBreadcrumbNode = { id: string; name: string };
type Props = { nodes: LocationBreadcrumbNode[]; unavailable?: boolean; onOpenFolder?: (id: string, ancestors: LocationBreadcrumbNode[]) => void };

export function LocationBreadcrumb({ nodes, unavailable, onOpenFolder }: Props) {
  if (!nodes.length || unavailable) return <span className="location-unavailable">Location unavailable</span>;
  const label = nodes.map(node => node.name).join(" " + String.fromCharCode(8250) + " ");
  return <span className="location-breadcrumb" title={label}>
    {nodes.map((node, index) => <span className="location-breadcrumb-node" key={node.id + "-" + index}>
      {index > 0 && <span className="location-breadcrumb-separator" aria-hidden="true">{String.fromCharCode(8250)}</span>}
      {onOpenFolder ? <button type="button" onClick={() => onOpenFolder(node.id, nodes.slice(0, index))} title={"Open " + node.name}>{node.name}</button> : <span>{node.name}</span>}
    </span>)}
  </span>;
}

export function itemLocationBreadcrumb(item: Asset | null): LocationBreadcrumbNode[] {
  if (!item) return [];
  if (item.location_breadcrumb?.length) return item.location_breadcrumb;
  if (!item.ancestor_ids?.length) return [];
  return item.ancestor_ids.map((id, index) => ({ id, name: item.ancestor_names?.[index] || "Folder" }));
}
