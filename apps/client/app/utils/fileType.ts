import googleDocsLogo from "../../assets/google-docs.png";
import googleSheetsLogo from "../../assets/google-sheets.png";
import type { Asset } from "../types";

export type FileType =
  | "folder"
  | "spreadsheet"
  | "document"
  | "presentation"
  | "form"
  | "drawing"
  | "pdf"
  | "image"
  | "video"
  | "text"
  | "file";

const GOOGLE_TYPES: Record<string, FileType> = {
  "application/vnd.google-apps.folder": "folder",
  "application/vnd.google-apps.spreadsheet": "spreadsheet",
  "application/vnd.google-apps.document": "document",
  "application/vnd.google-apps.presentation": "presentation",
  "application/vnd.google-apps.form": "form",
  "application/vnd.google-apps.drawing": "drawing",
};

export function getFileType(mimeType?: string | null, kind?: Asset["kind"], name?: string | null): FileType {
  const normalized = (mimeType || "").split(";", 1)[0].trim().toLowerCase();
  if (GOOGLE_TYPES[normalized]) return GOOGLE_TYPES[normalized];
  if (kind === "folder") return "folder";
  if (normalized === "application/pdf" || kind === "pdf") return "pdf";
  if (normalized.startsWith("image/") || kind === "image") return "image";
  if (normalized.startsWith("video/") || kind === "video") return "video";
  if (normalized === "text/plain" || /\.txt$/i.test(name || "")) return "text";
  if (kind === "document") return "document";
  return "file";
}

export function fileTypeLabel(type: FileType): string {
  return ({
    folder: "Folder",
    spreadsheet: "Google Sheet",
    document: "Google Doc",
    presentation: "Google Slides",
    form: "Google Form",
    drawing: "Google Drawing",
    pdf: "PDF",
    image: "Image",
    video: "Video",
    text: "Text file",
    file: "File",
  })[type];
}

export function fileTypeGlyph(type: FileType): string {
  return ({
    folder: "📁",
    spreadsheet: "▦",
    document: "▤",
    presentation: "▶",
    form: "☷",
    drawing: "◇",
    pdf: "PDF",
    image: "IMG",
    video: "VID",
    text: "TXT",
    file: "FILE",
  })[type];
}

export function fileTypeLogo(type: FileType): string | null { if (type === "document") return googleDocsLogo; if (type === "spreadsheet") return googleSheetsLogo; return null; }

export function fileTypeTone(type: FileType): string {
  return `asset-file-icon--${type}`;
}


export function isAvifAsset(asset: Partial<Pick<Asset, "mime_type" | "name">>): boolean {
  return (asset.mime_type || "").split(";", 1)[0].trim().toLowerCase() === "image/avif"
    || /\.avif$/i.test(asset.name || "");
}

export function isTextAsset(asset: Partial<Pick<Asset, "mime_type" | "name">>): boolean {
  const mime = (asset.mime_type || "").split(";", 1)[0].trim().toLowerCase();
  return mime === "text/plain" || /\.txt$/i.test(asset.name || "");
}

export function isPreviewableAsset(asset: Partial<Pick<Asset, "kind" | "mime_type" | "name">>): boolean {
  return asset.kind === "image" || asset.kind === "video" || isAvifAsset(asset) || isTextAsset(asset);
}
