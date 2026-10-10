export type AiOperationsActionName = "refresh" | "download" | "pause" | "test" | "edit" | "trash" | "plus" | "save" | "left" | "right" | "expand";

const paths: Record<AiOperationsActionName, React.ReactNode> = {
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.6 9A7 7 0 0 1 19 8M5 16a7 7 0 0 0 12.4-1" /></>,
  download: <><path d="M12 3v12m-4-4 4 4 4-4" /><path d="M4 17v3h16v-3" /></>,
  pause: <><rect x="5" y="4" width="5" height="16" rx="1" /><rect x="14" y="4" width="5" height="16" rx="1" /></>,
  test: <><path d="M4 12.5 9.2 18 20 6" /><circle cx="12" cy="12" r="9" /></>,
  edit: <><path d="m14 5 5 5M4 20l4.5-.9L19 8.6 15.4 5 4.9 15.5 4 20Z" /></>,
  trash: <><path d="M4 7h16M10 7V4h4v3M7 7l.8 13h8.4L17 7M10 11v5m4-5v5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  save: <><path d="M4 4h14l2 2v14H4V4Z M7 4v6h9V4" /><path d="M7 20v-7h10v7" /></>,
  left: <path d="m14.5 18-6-6 6-6" />,
  right: <path d="m9.5 18 6-6-6-6" />,
  expand: <><path d="M8 4H4v4M16 4h4v4M4 16v4h4M20 16v4h-4" /><path d="m4 4 6 6m10-6-6 6M4 20l6-6m10 6-6-6" /></>,
};

export function AiOperationsActionIcon({ name }: { name: AiOperationsActionName }) {
  return <svg className="ops-action-icon" data-ui-icon="true" viewBox="0 0 24 24" aria-hidden="true"
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {paths[name]}
  </svg>;
}