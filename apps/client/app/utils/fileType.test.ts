import { describe, expect, it } from "vitest";
import { fileTypeGlyph, fileTypeLabel, getFileType, isPreviewableAsset, isTextAsset } from "./fileType";

describe("Google Drive file type presentation", () => {
  it("maps Google Workspace MIME types to branded file types", () => {
    expect(getFileType("application/vnd.google-apps.spreadsheet")).toBe("spreadsheet");
    expect(getFileType("application/vnd.google-apps.document")).toBe("document");
    expect(getFileType("application/vnd.google-apps.presentation")).toBe("presentation");
  });

  it("provides readable labels and non-generic glyphs", () => {
    expect(fileTypeLabel("spreadsheet")).toBe("Google Sheet");
    expect(fileTypeGlyph("spreadsheet")).not.toBe("DOC");
    expect(fileTypeLabel("presentation")).toBe("Google Slides");
  });

  it("keeps media classification for previews", () => {
    expect(getFileType("image/avif", "other")).toBe("image");
    expect(getFileType("video/mp4", "other")).toBe("video");
  });
});

describe("TXT preview detection", () => {
  it("recognizes normalized text/plain MIME types and TXT names", () => {
    expect(isTextAsset({ mime_type: "text/plain" })).toBe(true);
    expect(isTextAsset({ mime_type: "text/plain; charset=utf-8" })).toBe(true);
    expect(isTextAsset({ name: "USER_PASS.TXT", mime_type: "application/octet-stream" })).toBe(true);
    expect(isTextAsset({ name: "file.json", mime_type: "application/octet-stream" })).toBe(false);
    expect(getFileType("application/octet-stream", "other", "notes.txt")).toBe("text");
  });

  it("only adds TXT to the existing previewable formats", () => {
    expect(isPreviewableAsset({ kind: "image" })).toBe(true);
    expect(isPreviewableAsset({ kind: "video" })).toBe(true);
    expect(isPreviewableAsset({ name: "notes.txt", mime_type: "application/octet-stream" })).toBe(true);
    expect(isPreviewableAsset({ name: "notes.json", kind: "other" })).toBe(false);
  });
});
