import { describe, expect, it } from "vitest";
import { sourceFolderBrand } from "./Icons";

describe("sourceFolderBrand", () => {
  it("recognizes Etsy folders case-insensitively", () => {
    expect(sourceFolderBrand("Etsy - HarleyEmbroidery")).toBe("etsy");
    expect(sourceFolderBrand("  etsy Pasimax")).toBe("etsy");
  });

  it("recognizes Amazon folders case-insensitively", () => {
    expect(sourceFolderBrand("Amazon - Collection Nurse")).toBe("amazon");
    expect(sourceFolderBrand("amazon")).toBe("amazon");
  });

  it("keeps generic folders on the standard folder icon", () => {
    expect(sourceFolderBrand("Brand Assets")).toBeNull();
    expect(sourceFolderBrand("NotAmazon folder")).toBeNull();
  });
});
