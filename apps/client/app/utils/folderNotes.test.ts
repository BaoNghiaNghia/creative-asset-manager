import { describe, expect, it } from "vitest";
import { folderNotePreview, productFolderKind } from "./folderNotes";

describe("product folder notes", () => {
  it("accepts only configured Amazon and Etsy folder names", () => {
    expect(productFolderKind("Amazon - B0GD6H8HYJ - Hoodie")).toBe("amazon");
    expect(productFolderKind("listing - 4347763062")).toBe("etsy");
    expect(productFolderKind("Etsy - 4347763062")).toBeNull();
  });
  it("uses an H1 or first meaningful line as a compact preview", () => {
    expect(folderNotePreview("# Product plan\nMore text")).toBe("Product plan");
    expect(folderNotePreview("  \n**Important** note")).toBe("Important note");
  });
});
