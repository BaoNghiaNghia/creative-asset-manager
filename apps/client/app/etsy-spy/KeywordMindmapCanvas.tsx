import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow, Background, Controls, Handle, MiniMap, Position, ReactFlowProvider,
  useReactFlow, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import "@xyflow/react/dist/style.css";
import type { KeywordForest } from "./keywordTree";
import {
  type KeywordGroup, type KeywordGroupForest, type KeywordGroupProjection,
  type KeywordGroupVisible, visibleKeywordGroups,
} from "./keywordMindmap";

const NODE_WIDTH = 248;
const NODE_HEIGHT = 88;
const HUB_WIDTH = 180;
const HUB_HEIGHT = 84;
const MAX_VISIBLE = 260;

type MapNodeData = Record<string, unknown> & {
  label: string;
  status: string;
  depth: number;
  sessions: number;
  occurrences: number;
  branchRecords: number;
  listings: number | null;
  children: number;
  isOpen: boolean;
  selected: boolean;
  missingParent: boolean;
  onToggle?: (id: string) => void;
  total?: number;
  unique?: number;
};
type MapFlowNode = Node<MapNodeData, "keyword" | "hub">;

function KeywordCard({ id, data }: NodeProps<MapFlowNode>) {
  return <>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className={"etsy-map-node " + (data.selected ? "is-selected " : "") +
      (data.missingParent ? "is-missing" : "")}
      title={data.label + " · " + data.occurrences + " original node(s)"}>
      <div className="etsy-map-node-top">
        <span className={"etsy-map-status " + data.status.toLowerCase()}>
          {data.status === "mixed" ? "Mixed" : data.status === "missing" ? "Unsynced"
            : data.status}
        </span>
        {data.occurrences > 1 && <span className="etsy-map-occurrences" title="Merged original keyword records">
          {data.occurrences}×
        </span>}
      </div>
      <div className="etsy-map-node-title-row">
        <span className="etsy-map-node-symbol" aria-hidden="true">{data.depth === 0 ? "▱" : "▤"}</span>
        <strong className="etsy-map-node-title">{data.label}</strong>
      </div>
      <div className="etsy-map-node-bottom">
        <span>{data.sessions} session{data.sessions !== 1 ? "s" : ""}</span>
        <span>{data.listings ?? "—"} listings</span>
        {data.children > 0 && <button type="button"
          className="etsy-map-expand nodrag nopan"
          onPointerDown={event => event.stopPropagation()}
          aria-expanded={data.isOpen} aria-label={(data.isOpen ? "Collapse " : "Expand ") + data.label}
          title={data.isOpen ? "Collapse branch" : "Expand branch"}
          onClick={event => { event.stopPropagation(); data.onToggle?.(id); }}>
          {data.isOpen ? "−" : "+"}<span>{data.children}</span>
        </button>}
      </div>
    </div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </>;
}

function HubCard({ data }: NodeProps<MapFlowNode>) {
  return <>
    <div className="etsy-map-hub">
      <span>CAM · ETSY SPY</span>
      <strong>Keyword Map</strong>
      <small>{data.total?.toLocaleString()} records · {data.unique?.toLocaleString()} groups</small>
    </div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </>;
}

const NODE_TYPES = { keyword: KeywordCard, hub: HubCard };

function createFlowLayout(
  visible: KeywordGroupVisible[], forest: KeywordGroupForest,
  onToggle: (id: string) => void, selected: string | null,
): { nodes: MapFlowNode[]; edges: Edge[] } {
  const hub: MapFlowNode = {
    id: "keyword-map-hub", type: "hub", position: { x: 0, y: 0 },
    data: { label: "Keyword Map", status: "hub", depth: -1, sessions: 0, occurrences: 0,
      branchRecords: 0, listings: null, children: 0, isOpen: true,
      selected: false, missingParent: false,
      total: forest.rawCount, unique: forest.realGroups },
    draggable: false, selectable: false,
  };
  const nodes: MapFlowNode[] = [hub];
  const edges: Edge[] = [];
  const visibleKeys = new Set(visible.map(row => row.group.key));
  for (const { group, level, isOpen } of visible) {
    nodes.push({
      id: group.key, type: "keyword",
      position: { x: level * (NODE_WIDTH + 80), y: 0 },
      draggable: false,
      data: {
        label: group.keyword, status: group.status, depth: group.depth,
        sessions: group.sessions.length,
        occurrences: group.members.length,
        branchRecords: group.branchRecords, listings: group.matchedCount,
        children: group.children.length, isOpen, selected: selected === group.key,
        missingParent: group.missingParent, onToggle,
      },
    });
    const source = group.parentKey && visibleKeys.has(group.parentKey)
      ? group.parentKey : hub.id;
    edges.push({
      id: JSON.stringify([source, group.key]), source, target: group.key,
      type: "smoothstep", selectable: false,
      style: { stroke: group.missingParent ? "#d5b47a" : "#9db3ce", strokeWidth: 1.65 },
    });
  }
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "LR", align: "UL", nodesep: 22, ranksep: 78,
    marginx: 36, marginy: 32,
  });
  for (const node of nodes)
    graph.setNode(node.id, { width: node.type === "hub" ? HUB_WIDTH : NODE_WIDTH,
      height: node.type === "hub" ? HUB_HEIGHT : NODE_HEIGHT });
  for (const edge of edges) graph.setEdge(edge.source, edge.target);
  dagre.layout(graph);
  for (const node of nodes) {
    const point = graph.node(node.id);
    const width = node.type === "hub" ? HUB_WIDTH : NODE_WIDTH;
    const height = node.type === "hub" ? HUB_HEIGHT : NODE_HEIGHT;
    node.position = { x: point.x - width / 2, y: point.y - height / 2 };
  }
  return { nodes, edges };
}

function FlowCanvas({
  nodes, edges, onSelect, layoutKey,
}: {
  nodes: MapFlowNode[]; edges: Edge[]; onSelect: (key: string) => void;
  layoutKey: string;
}) {
  const { fitView, getViewport, setCenter } = useReactFlow<MapFlowNode, Edge>();
  useEffect(() => {
    let active = true;
    // Large Scout branches should not shrink every label to a few pixels.
    // Keep the viewport legible; pan/zoom and the minimap reveal the remainder.
    const timeout = window.setTimeout(() => {
      void fitView({ padding: 0.15, maxZoom: 1, duration: 180 }).then(() => {
        if (!active || getViewport().zoom >= 0.58) return;
        const hub = nodes.find(node => node.type === "hub");
        if (hub) {
          void setCenter(hub.position.x + HUB_WIDTH / 2 + 150,
            hub.position.y + HUB_HEIGHT / 2, { zoom: 0.62, duration: 180 });
        }
      });
    }, 35);
    return () => { active = false; window.clearTimeout(timeout); };
  }, [fitView, getViewport, setCenter, layoutKey]);

  return <ReactFlow<MapFlowNode, Edge>
    nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
    nodesDraggable={false} nodesConnectable={false}
    elementsSelectable onNodeClick={(_event, node) => {
      if (node.type === "keyword") onSelect(node.id);
    }}
    minZoom={0.15} maxZoom={1.8}
    fitView fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
    onlyRenderVisibleElements
    panOnDrag zoomOnScroll zoomOnPinch zoomOnDoubleClick
    proOptions={{ hideAttribution: false }} aria-label="Interactive keyword mindmap">
    <Background color="#d9e5f4" gap={20} size={1} />
    <Controls showInteractive={false} position="bottom-left" />
    <MiniMap pannable zoomable position="bottom-right"
      nodeColor={node => node.type === "hub" ? "#6f94cd" :
        (node.data as MapNodeData).status === "failed" ? "#d88c83" : "#94c6b3"}
      maskColor="rgba(244, 247, 252, .72)" />
  </ReactFlow>;
}

function KeywordInspector({
  selected, grouped, raw, onClose, onFocus, onSelect, focused,
}: {
  selected: KeywordGroup | null; grouped: KeywordGroupForest; raw: KeywordForest;
  onClose: () => void; onFocus: (id: string | null) => void;
  onSelect: (id: string) => void; focused: string | null;
}) {
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { setShowAll(false); }, [selected?.key]);
  const path: string[] = [];
  let pointer = selected?.parentKey ?? null;
  while (pointer && path.length < 24) {
    const ancestor = grouped.nodes.get(pointer);
    if (!ancestor) break;
    path.unshift(ancestor.keyword);
    pointer = ancestor.parentKey;
  }
  const sourceMembers = selected?.members.map(key => raw.nodes.get(key)).filter(
    (node): node is NonNullable<typeof node> => Boolean(node && !node.missingParent),
  ) || [];
  const list = showAll ? sourceMembers : sourceMembers.slice(0, 20);
  // The same wording may also appear under a different parent. Preserve
  // both paths rather than creating a false parent-child graph merge.
  const otherBranches = selected
    ? [...grouped.nodes.values()].filter(group =>
      group.key !== selected.key && group.normalized === selected.normalized).slice(0, 8)
    : [];
  return <aside className="etsy-map-inspector" aria-label="Keyword detail panel">
    <div className="etsy-map-inspector-heading">
      <strong>Keyword details</strong>
      {selected && <button type="button" onClick={onClose} aria-label="Close keyword details">×</button>}
    </div>
    {!selected ? <div className="etsy-map-inspector-empty">
      <strong>Select a keyword node</strong>
      <p>Click a node to view original sessions, statuses, evidence and child branches. Use + to expand a branch.</p>
    </div> : <>
      <h3>{selected.keyword}</h3>
      {path.length > 0 && <small className="etsy-map-breadcrumb">{path.join(" › ")}</small>}
      <div className="etsy-map-detail-stats">
        <div><strong>{sourceMembers.length}</strong><span>Records merged</span></div>
        <div><strong>{selected.sessions.length}</strong><span>Sessions</span></div>
        <div><strong>{selected.children.length}</strong><span>Child branches</span></div>
        <div><strong>{selected.matchedCount ?? "—"}</strong><span>Max listings*</span></div>
      </div>
      <div className="etsy-map-inspector-actions">
        <button type="button" onClick={() => onFocus(focused === selected.key ? null : selected.key)}>
          {focused === selected.key ? "Show all branches" : "Focus branch"}
        </button>
      </div>
      <small className="etsy-map-metric-note">
        * Listing count uses the maximum reported per original node to avoid double-counting sessions.
      </small>
      <div className="etsy-map-status-breakdown">
        {Object.entries(selected.statusCounts).map(([status, count]) =>
          <span key={status} className={"etsy-map-status " + status}>{status} · {count}</span>)}
      </div>
      {otherBranches.length > 0 && <section className="etsy-map-aliases">
        <strong>Same keyword in other branches</strong>
        <div>{otherBranches.map(group => <button type="button" key={group.key}
          onClick={() => { onFocus(null); onSelect(group.key); }}
          title={group.keyword}>
          Another branch · {group.members.length} record{group.members.length !== 1 ? "s" : ""}
        </button>)}</div>
      </section>}
      <strong className="etsy-map-records-title">Original Scout records</strong>
      {sourceMembers.length === 0 && <p className="etsy-map-metric-note">Parent record is not yet synced.</p>}
      <div className="etsy-map-records">
        {list.map(node => <details className="etsy-map-record" key={node.key}>
          <summary>
            <span>{node.item.sessionId || "Unknown session"}</span>
            <span className={"etsy-map-status " + (node.item.status || "pending").toLowerCase()}>
              {node.item.status || "pending"}
            </span>
          </summary>
          <div>Priority: {node.item.priority ?? "—"} · Listings: {node.item.matchedCount ?? "—"}</div>
          <div>Depth: {node.item.depth ?? "—"} · ID: {node.item.id}</div>
          {node.item.updatedAt && <div>Updated: {new Date(node.item.updatedAt).toLocaleString("vi-VN")}</div>}
          {node.item.reason && <p>{node.item.reason}</p>}
          {node.item.evidence != null && <pre>{JSON.stringify(node.item.evidence, null, 2)}</pre>}
          {node.item.sourceUrl && <a href={node.item.sourceUrl}
            target="_blank" rel="noopener noreferrer">Open source ↗</a>}
        </details>)}
      </div>
      {sourceMembers.length > 20 && <button className="etsy-map-show-all"
        type="button" onClick={() => setShowAll(v => !v)}>
        {showAll ? "Show fewer records" : "Show all " + sourceMembers.length + " records"}
      </button>}
    </>}
  </aside>;
}

export function KeywordMindmapCanvas({
  forest, raw, projection, expanded, onToggle,
}: {
  forest: KeywordGroupForest; raw: KeywordForest;
  projection: KeywordGroupProjection; expanded: ReadonlySet<string>;
  onToggle: (id: string) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  useEffect(() => {
    if (focused && !projection.visible.has(focused)) setFocused(null);
  }, [focused, projection]);
  useEffect(() => {
    if (selected && !forest.nodes.has(selected)) setSelected(null);
    if (focused && !forest.nodes.has(focused)) setFocused(null);
  }, [forest, selected, focused]);

  const visible = useMemo(() =>
    visibleKeywordGroups(forest, projection, expanded, { focus: focused, limit: MAX_VISIBLE }),
  [forest, projection, expanded, focused]);
  const layout = useMemo(() => createFlowLayout(visible.rows, forest, onToggle, selected),
    [visible.rows, forest, onToggle, selected]);
  const selectedGroup = selected ? forest.nodes.get(selected) || null : null;
  const layoutKey = visible.rows.map(row => row.group.key).join("|");

  return <div className="etsy-map-layout">
    <div className="etsy-map-main">
      <div className="etsy-map-canvas-toolbar">
        <span>{visible.rows.length.toLocaleString("vi-VN")} visible groups · {forest.mergedRecords.toLocaleString("vi-VN")} duplicate records combined</span>
        {focused && <button type="button" onClick={() => setFocused(null)}>← All branches</button>}
      </div>
      {visible.hidden > 0 && <p role="status" className="etsy-map-overflow">
        Showing {MAX_VISIBLE} nodes for smooth navigation; {visible.hidden.toLocaleString("vi-VN")} more expanded
        nodes are hidden. Focus a branch, collapse other branches or narrow the search to view them.
      </p>}
      <div className="etsy-map-canvas" data-testid="keyword-mindmap-canvas">
        <ReactFlowProvider>
          <FlowCanvas nodes={layout.nodes} edges={layout.edges}
            layoutKey={layoutKey} onSelect={setSelected} />
        </ReactFlowProvider>
      </div>
      <div className="etsy-map-footer">Drag to pan · Scroll to zoom · Click a node for details · + to expand its branch</div>
    </div>
    <KeywordInspector selected={selectedGroup} grouped={forest} raw={raw}
      focused={focused} onFocus={setFocused} onSelect={setSelected}
      onClose={() => setSelected(null)} />
  </div>;
}