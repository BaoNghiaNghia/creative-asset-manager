export function ChevronIcon({ expanded = false }: { expanded?: boolean }) {
  return <svg className={"chevron-icon " + (expanded ? "expanded" : "")} viewBox="0 0 16 16" aria-hidden="true">
    <path d="m6 3.5 4.5 4.5L6 12.5" />
  </svg>;
}

export function DriveIcon() {
  return <svg className="drive-icon" viewBox="0 0 20 20" aria-hidden="true">
    <path d="M6.2 2.5h5.1l5.9 10.2-2.6 4.6H9.4l2.6-4.6h5.2" />
    <path d="m6.2 2.5-5.8 10.2L3 17.3h6.4L12 12.7 6.2 2.5Z" />
  </svg>;
}

export function FolderTreeIcon() {
  return <svg className="folder-tree-icon" viewBox="0 0 18 18" aria-hidden="true">
    <path d="M2.5 5.25h5l1.3 1.5h6.7v7.75h-13Z" />
    <path d="M2.5 5.25V3.5h4.1l1.3 1.75" />
  </svg>;
}

export function SidebarIcon({ open }: { open: boolean }) {
  return <svg viewBox="0 0 18 18" aria-hidden="true">
    <rect x="2.25" y="3" width="13.5" height="12" rx="1.5" />
    <path d="M6.25 3v12" />
    <path d={open ? "m11 6-3 3 3 3" : "m9 6 3 3-3 3"} />
  </svg>;
}
