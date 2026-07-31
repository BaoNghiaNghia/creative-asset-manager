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
  | "file";

const GOOGLE_TYPES: Record<string, FileType> = {
  "application/vnd.google-apps.folder": "folder",
  "application/vnd.google-apps.spreadsheet": "spreadsheet",
  "application/vnd.google-apps.document": "document",
  "application/vnd.google-apps.presentation": "presentation",
  "application/vnd.google-apps.form": "form",
  "application/vnd.google-apps.drawing": "drawing",
};

export function getFileType(mimeType?: string | null, kind?: Asset["kind"]): FileType {
  const normalized = (mimeType || "").split(";", 1)[0].trim().toLowerCase();
  if (GOOGLE_TYPES[normalized]) return GOOGLE_TYPES[normalized];
  if (kind === "folder") return "folder";
  if (normalized === "application/pdf" || kind === "pdf") return "pdf";
  if (normalized.startsWith("image/") || kind === "image") return "image";
  if (normalized.startsWith("video/") || kind === "video") return "video";
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
    file: "File",
  })[type];
}

export function fileTypeGlyph(type: FileType): string {
  return ({
    folder: "DIR",
    spreadsheet: "▦",
    document: "▤",
    presentation: "▶",
    form: "☷",
    drawing: "◇",
    pdf: "PDF",
    image: "IMG",
    video: "VID",
    file: "FILE",
  })[type];
}

export function fileTypeTone(type: FileType): string {
  return `asset-file-icon--${type}`;
}
