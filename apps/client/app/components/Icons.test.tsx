import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { amazonAsin, BrandIcon, etsyListingId, GridViewIcon, ListViewIcon, SidebarIcon, sourceFolderBrand } from "./Icons";

describe("BrandIcon", () => {
  it("renders the bundled Creative Asset Manager artwork without a remote dependency", () => {
    const markup = renderToStaticMarkup(<BrandIcon />);
    expect(markup).toContain('class="brand-logo"');
    expect(markup).toContain("<img");
    expect(markup).toContain("/assets/logos/creative-assets-icon.png");
  });
});

describe("system UI icons", () => {
  it("renders the redesigned sidebar collapse icon with panel and chevron geometry", () => {
    const markup = renderToStaticMarkup(<SidebarIcon open />);
    expect(markup).toContain('viewBox="0 0 24 24"');
    expect(markup).toContain('width="17.5"');
    expect(markup).toContain("15.2 8.4");
  });

  it("renders vector grid and list view icons instead of text glyphs", () => {
    const grid = renderToStaticMarkup(<GridViewIcon />);
    const list = renderToStaticMarkup(<ListViewIcon />);
    expect(grid).toContain('class="layout-view-icon"');
    expect(grid).toContain("<rect");
    expect(list).toContain('class="layout-view-icon"');
    expect((list.match(/<circle/g) || []).length).toBe(3);
  });
});

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


describe("etsyListingId", () => {
  it("extracts a listing id from an Etsy child folder title", () => {
    expect(etsyListingId("listing - 4343675953")).toBe("4343675953");
    expect(etsyListingId("Listing - 4343675953 - Assets")).toBe("4343675953");
  });

  it("rejects non-listing folder names", () => {
    expect(etsyListingId("Optimized assets")).toBeNull();
    expect(etsyListingId("Etsy - HarleyEmbroidery")).toBeNull();
  });
});
