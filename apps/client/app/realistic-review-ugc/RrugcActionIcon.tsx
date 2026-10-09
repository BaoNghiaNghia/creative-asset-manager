import type { ReactNode } from "react";

/** Consistent, decorative action glyphs for the Realistic Review UGC workflow.
 * Keep the visible text label: icons supplement rather than replace it.
 */
export type RrugcActionIconName =
  | "scan" | "refresh" | "skills" | "sparkles" | "play"
  | "stop" | "select-all" | "deselect" | "chevron-left" | "chevron-right" | "chevrons-left" | "chevrons-right"
  | "folder" | "sort" | "filter" | "check" | "logs";

const paths: Record<RrugcActionIconName, ReactNode> = {
  scan: <><path d="M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3M4 16v3a1 1 0 0 0 1 1h3m8 0h3a1 1 0 0 0 1-1v-3" /><circle cx="10.5" cy="10.5" r="4.5" /><path d="m14 14 4 4" /></>,
  refresh: <><path d="M20 11a8 8 0 1 0-2.5 6" /><path d="M20 4v7h-7" /></>,
  skills: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 9h8M8 13h8M8 17h5" /></>,
  sparkles: <><path d="m12 2 1.9 6.1L20 10l-6.1 1.9L12 18l-1.9-6.1L4 10l6.1-1.9L12 2ZM19 17v5m-2.5-2.5h5" /></>,
  play: <><circle cx="12" cy="12" r="9" /><path d="m10 8 6 4-6 4V8Z" /></>,
  stop: <><circle cx="12" cy="12" r="9" /><rect x="9" y="9" width="6" height="6" rx=".8" /></>,
  "select-all": <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="m7 12 3 3 7-7" /></>,
  deselect: <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="m8 9 8 6m0-6-8 6" /></>,
  "chevron-left": <path d="m15 5-7 7 7 7" />,
  "chevron-right": <path d="m9 5 7 7-7 7" />,
  "chevrons-left": <><path d="m11 5-7 7 7 7m8-14-7 7 7 7" /></>,
  "chevrons-right": <><path d="m5 5 7 7-7 7m8-14 7 7-7 7" /></>,
  folder: <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />,
  sort: <><path d="M8 4v16m-4-4 4 4 4-4m4 4V4m-4 4 4-4 4 4" /></>,
  filter: <><path d="M4 5h16l-6.5 7.5V20l-3-1.5v-6L4 5Z" /></>,
  check: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
  logs: <><path d="M7 3h10l3 3v15H4V3h3Z" /><path d="M8 10h8M8 14h8M8 18h5" /></>,
};

export function RrugcActionIcon({ name }: { name: RrugcActionIconName }) {
  return <svg data-ui-icon={name} aria-hidden="true" focusable="false"
    className="rrugc-action-icon" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {paths[name]}
  </svg>;
}
