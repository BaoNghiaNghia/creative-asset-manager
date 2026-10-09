export type WorkflowIconName =
  | "settings" | "close" | "heartbeat" | "shield-check" | "alert" | "clock"
  | "layers" | "download" | "sparkles" | "power" | "monitor" | "wifi"
  | "wifi-off" | "search" | "key" | "trending-up" | "thumbs-up"
  | "thumbs-down" | "activity" | "list" | "check" | "refresh"
  | "image" | "copy" | "error" | "play" | "circle-pause";

export function WorkflowStatusIcon({ name, size = 16 }: { name: WorkflowIconName; size?: number }) {
  const paths: Record<WorkflowIconName, React.ReactNode> = {
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="2" fill="currentColor" stroke="none" /><circle cx="15" cy="17" r="2" fill="currentColor" stroke="none" /></>,
    close: <path d="M6 6l12 12M18 6 6 18" />,
    heartbeat: <><path d="M3 12h4l3-6 4 12 3-6h4" /></>,
    "shield-check": <><path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5z" /><path d="m8 12 3 3 5-5" /></>,
    alert: <><path d="M12 3 2.5 20h19L12 3Z" /><path d="M12 9v5m0 3v.5" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    layers: <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M7 9h10M7 13h7" /></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4M4 18v3h16v-3" /></>,
    sparkles: <><path d="m12 2 2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2L12 2Z" /></>,
    power: <><path d="M12 3v9M6 6.5a9 9 0 1 0 12 0" /></>,
    monitor: <><rect x="3" y="4" width="18" height="14" rx="2" /><path d="M8 22h8m-4-4v4" /></>,
    wifi: <><path d="M2 8a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M9 16a4 4 0 0 1 6 0" /><circle cx="12" cy="20" r="1" fill="currentColor" stroke="none" /></>,
    "wifi-off": <><path d="M2 8a15 15 0 0 1 12-3M5 12a10 10 0 0 1 12-1M9 16a4 4 0 0 1 6 0M2 2l20 20" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    key: <><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9m-4 1 4 4m-6 2 3 3" /></>,
    "trending-up": <><path d="m3 17 7-7 4 4 7-7m-6 0h6v6" /></>,
    "thumbs-up": <><path d="M7 10v10H4a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h3Zm0 0 5-7a2 2 0 0 1 3 2l-1 4h5a3 3 0 0 1 3 4l-1 5a3 3 0 0 1-3 2H7" /></>,
    "thumbs-down": <><path d="M7 14V4H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h3Zm0 0 5 7a2 2 0 0 0 3-2l-1-4h5a3 3 0 0 0 3-4l-1-5a3 3 0 0 0-3-2H7" /></>,
    activity: <><path d="M2 12h4l3-5 5 10 3-5h5" /></>,
    list: <><path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    refresh: <><path d="M20 8a8 8 0 0 0-13.5-2L4 8M4 4v4h4M4 16a8 8 0 0 0 13.5 2L20 16m-4 0h4v4" /></>,
    image: <><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="8" cy="9" r="1" /><path d="m4 18 6-6 4 4 3-3 4 4" /></>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></>,
    error: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6m0 4v.5" /></>,
    play: <path d="m8 5 11 7-11 7z" />,
    "circle-pause": <><circle cx="12" cy="12" r="9" /><path d="M9 8v8m6-8v8" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" data-workflow-icon={name}>{paths[name]}</svg>;
}
