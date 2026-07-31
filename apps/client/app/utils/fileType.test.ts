import { describe, expect, it } from "vitest";
import { fileTypeGlyph, fileTypeLabel, getFileType } from "./fileType";

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
