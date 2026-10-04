import brandIconUrl from "../../assets/logos/creative-assets-icon.png";

export function BrandIcon() {
  return <img className="brand-logo" src={brandIconUrl} alt="" aria-hidden="true" />;
}

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
  return <svg className="etsy-logo marketplace-logo marketplace-logo-full" viewBox="0 0 110 46" aria-hidden="true" focusable="false">
    <text x="3" y="34" fontFamily="Georgia, 'Times New Roman', serif" fontSize="39" fontWeight="700" fill="#F1641E">Etsy</text>
  </svg>;
}

export function EtsyCompactIcon() {
  return <svg className="source-folder-brand source-folder-brand-etsy marketplace-compact-icon" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
    <rect x="2" y="2" width="44" height="44" rx="11" fill="#F1641E" />
    <text x="24" y="36" textAnchor="middle" fontFamily="Georgia, 'Times New Roman', serif" fontSize="37" fontWeight="700" fill="#fff">E</text>
  </svg>;
}

export function etsyListingId(name: string): string | null {
  const match = name.trim().match(/^listing\s*-\s*(\d{6,})\b/i);
  return match?.[1] ?? null;
}

export function AmazonLogo() {
  return <svg className="amazon-logo marketplace-logo marketplace-logo-full" viewBox="0 0 132 48" aria-hidden="true" focusable="false">
    <text x="4" y="31" fontFamily="Arial, Helvetica, sans-serif" fontSize="34" fontWeight="700" letterSpacing="-1.1" fill="#131A22">amazon</text>
    <path d="M24 36.5C48 45 82 45.4 108 35.6" fill="none" stroke="#FF9900" strokeWidth="4.3" strokeLinecap="round" />
    <path d="m103.5 33.7 9.2.9-5.4 7.2" fill="none" stroke="#FF9900" strokeWidth="4.3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export function AmazonCompactIcon() {
  return <svg className="source-folder-brand source-folder-brand-amazon marketplace-compact-icon" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
    <text x="8" y="31" fontFamily="Arial, Helvetica, sans-serif" fontSize="32" fontWeight="800" fill="#131A22">a</text>
    <path d="M9.5 36.2c8.6 4.2 19.6 4.6 29.4.2" fill="none" stroke="#FF9900" strokeWidth="3.4" strokeLinecap="round" />
    <path d="m35.2 34.9 6.1.5-3.7 5" fill="none" stroke="#FF9900" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
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

  if (brand === "etsy") return <EtsyCompactIcon />;
  if (brand === "amazon") return <AmazonCompactIcon />;
  return <FolderTreeIcon />;
}

export function SidebarIcon({ open }: { open: boolean }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="3.25" y="4" width="17.5" height="16" rx="3" />
    <path d="M8.2 4.25v15.5" />
    <path d={open ? "m15.2 8.4-3.6 3.6 3.6 3.6" : "m12.8 8.4 3.6 3.6-3.6 3.6"} />
  </svg>;
}

export function GridViewIcon() {
  return <svg className="layout-view-icon" viewBox="0 0 24 24" aria-hidden="true">
    <rect x="3.5" y="3.5" width="17" height="17" rx="2.6" />
    <path d="M8.7 3.8v16.4M15.3 3.8v16.4M3.8 8.7h16.4M3.8 15.3h16.4" />
  </svg>;
}

export function ListViewIcon() {
  return <svg className="layout-view-icon" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M9.2 6.5h10.3M9.2 12h10.3M9.2 17.5h10.3" />
    <circle cx="5.25" cy="6.5" r="1.15" />
    <circle cx="5.25" cy="12" r="1.15" />
    <circle cx="5.25" cy="17.5" r="1.15" />
  </svg>;
}
