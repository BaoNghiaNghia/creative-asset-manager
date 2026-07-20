import { useState } from "react";

type Props = { value: unknown; maxDepth?: number; maxNodes?: number };

function renderNode(value: unknown, depth: number, budget: { value: number }, maxDepth: number): React.ReactNode {
  if (budget.value-- <= 0 || depth >= maxDepth) return <em>[truncated]</em>;
  if (value === null || typeof value !== "object") return <span>{typeof value === "string" ? value : JSON.stringify(value)}</span>;
  const entries = Array.isArray(value) ? value.map((item, index) => [String(index), item] as const) : Object.entries(value as Record<string, unknown>);
  return <ul>{entries.map(([key, item]) => <li key={key}><b>{key}</b>: {renderNode(item, depth + 1, budget, maxDepth)}</li>)}</ul>;
}

export function SafeJsonTree({ value, maxDepth = 10, maxNodes = 1500 }: Props) {
  const [expanded, setExpanded] = useState(false);
  if (value === null || value === undefined) return <p className="muted">No document available.</p>;
  return <div className={"json-tree " + (expanded ? "expanded" : "")}>
    {renderNode(value, 0, { value: maxNodes }, maxDepth)}
    <button type="button" onClick={() => setExpanded(value => !value)}>{expanded ? "Collapse" : "Expand viewer"}</button>
  </div>;
}
