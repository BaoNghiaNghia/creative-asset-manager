export type ProductFolderKind = "amazon" | "etsy";

const AMAZON = /^Amazon\s*-\s*([A-Za-z0-9]{10})(?:\s*-\s*(.*))?$/i;
const ETSY = /^listing\s*-\s*(\d+)(?:\s*-\s*(.*))?$/i;

export function productFolderKind(name: string): ProductFolderKind | null {
  if (AMAZON.test(name.trim())) return "amazon";
  if (ETSY.test(name.trim())) return "etsy";
  return null;
}

export function folderNotePreview(markdown: string, maxLength = 88): string {
  const first = markdown.split(/\r?\n/).map(line => line.trim()).find(Boolean) || "";
  const text = first.replace(/^#{1,6}\s+/, "").replace(/[*_`~\[\]]/g, "").replace(/\([^)]*\)/g, "").trim();
  return text.length > maxLength ? text.slice(0, maxLength - 1).trimEnd() + "&" : text;
}
