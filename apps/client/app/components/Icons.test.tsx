import { describe, expect, it } from "vitest";
import { amazonAsin, sourceFolderBrand } from "./Icons";

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


describe("amazonAsin", () => {
  it("extracts an ASIN from an Amazon folder title", () => {
    expect(amazonAsin("Amazon - B0GD6H8HYJ - Hoodies The Moon And Back Set")).toBe("B0GD6H8HYJ");
    expect(amazonAsin("amazon - b0grz9rkb4 - product")).toBe("B0GRZ9RKB4");
  });

  it("rejects titles without an Amazon ASIN prefix", () => {
    expect(amazonAsin("Amazon - Collection Nurse")).toBeNull();
    expect(amazonAsin("Etsy - B0GD6H8HYJ - Product")).toBeNull();
  });
});
