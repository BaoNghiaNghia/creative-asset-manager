import amazonLogoUrl from "../../assets/logos/amazon-logo.svg";

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

export function SharePointIcon() {
  return <svg className="drive-icon sharepoint-icon" viewBox="0 0 20 20" aria-hidden="true">
    <rect x="2" y="3" width="10" height="14" rx="2" />
    <path d="M12 6h6v8h-6M5.5 8.25c.6-.55 2.6-.55 3.1.15.45.65-.05 1.05-1.45 1.35-1.35.3-1.85.75-1.35 1.45.55.75 2.55.75 3.15.05" />
  </svg>;
}

export function FolderTreeIcon() {
  return <svg className="folder-tree-icon" viewBox="0 0 18 18" aria-hidden="true">
    <path d="M2.5 5.25h5l1.3 1.5h6.7v7.75h-13Z" />
    <path d="M2.5 5.25V3.5h4.1l1.3 1.75" />
  </svg>;
}

export function EtsyLogo() {
  return <svg className="etsy-logo" viewBox="0 0 64 30" aria-hidden="true">
    <text x="1" y="22" fontFamily="Georgia, serif" fontSize="25" fill="currentColor">Etsy</text>
  </svg>;
}

export function etsyListingId(name: string): string | null {
  const match = name.trim().match(/^listing\s*-\s*(\d{6,})\b/i);
  return match?.[1] ?? null;
}

export function AmazonLogo() {
  return <img className="amazon-logo" src={amazonLogoUrl} alt="" aria-hidden="true" />;
}

export function amazonAsin(name: string): string | null {
  const match = name.trim().match(/^amazon\s*-\s*([a-z0-9]{10})(?:\s*-\s|$)/i);
  return match?.[1].toUpperCase() ?? null;
}

export type SourceFolderBrand = "etsy" | "amazon" | null;

export function sourceFolderBrand(name: string): SourceFolderBrand {
  const normalized = name.trim().toLowerCase();
  return (normalized.match(/^(etsy|amazon)(?:\s|[-]|$)/)?.[1] as Exclude<SourceFolderBrand, null> | undefined) ?? null;
}

/** Uses a provider mark for marketplace folders and the folder glyph otherwise. */
export function SourceFolderIcon({ name }: { name: string }) {
  const brand = sourceFolderBrand(name);

  if (brand === "etsy") {
    return <span className="source-folder-brand source-folder-brand-etsy" aria-hidden="true">e</span>;
  }
  if (brand === "amazon") {
    return <span className="source-folder-brand source-folder-brand-amazon" aria-hidden="true">a</span>;
  }
  return <FolderTreeIcon />;
}

export function SidebarIcon({ open }: { open: boolean }) {
  return <svg viewBox="0 0 18 18" aria-hidden="true">
    <rect x="2.25" y="3" width="13.5" height="12" rx="1.5" />
    <path d="M6.25 3v12" />
    <path d={open ? "m11 6-3 3 3 3" : "m9 6 3 3-3 3"} />
  </svg>;
}
