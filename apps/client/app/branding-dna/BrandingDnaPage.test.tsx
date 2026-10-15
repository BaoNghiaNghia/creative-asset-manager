import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BrandingDnaPage, brandingDnaTabForPath } from "./BrandingDnaPage";
import { BrandingDnaFoundation } from "./BrandingDnaFoundation";
import { BrandingDnaWorkers } from "./BrandingDnaWorkers";

describe("Branding DNA worker control plane preview", () => {
  it("maps supported nested routes while rejecting arbitrary tab names", () => {
    expect(brandingDnaTabForPath("/branding-dna")).toBe("overview");
    expect(brandingDnaTabForPath("/branding-dna/shops")).toBe("shops");
    expect(brandingDnaTabForPath("/branding-dna/scanner")).toBe("scanner");
    expect(brandingDnaTabForPath("/branding-dna/products")).toBe("products");
    expect(brandingDnaTabForPath("/branding-dna/dna")).toBe("dna");
    expect(brandingDnaTabForPath("/branding-dna/evil?next=https://attacker.test")).toBe("overview");
  });

  it("shows tenant shop and worker controls while preview opens in separate tab", () => {
    const html = renderToStaticMarkup(<BrandingDnaPage />);
    expect(html).toContain("workspace-page-header");
    expect(html).toContain("Branding DNA workflow");
    expect(html).toContain("SHOPS SAVED");
    expect(html).toContain("Mở Shop Library");
    expect(html).toContain("Dedicated Windows Worker");
    expect(html).not.toContain("<iframe");
    expect(html).toContain('target="_blank"');
    expect(html).toContain("/branding-dna-preview/index.html?view=overview");
    expect(html).toContain("Scanner not connected");
  });

  it("separates Overview summaries from full Shop Library controls", () => {
    const overview = renderToStaticMarkup(<BrandingDnaFoundation compact />);
    const library = renderToStaticMarkup(<BrandingDnaFoundation />);
    expect(overview).toContain("Mở Shop Library");
    expect(overview).toContain("SHOPS SAVED");
    expect(overview).not.toContain("+ Thêm shop");
    expect(library).toContain("+ Thêm shop");
    expect(library).toContain("Shop Registry");
  });

  it("shows full worker enrollment only on the Browser Scanner stage", () => {
    const overview = renderToStaticMarkup(<BrandingDnaWorkers compact />);
    const scanner = renderToStaticMarkup(<BrandingDnaWorkers />);
    expect(overview).toContain("Mở Browser Scanner");
    expect(overview).not.toContain('class="bdna-worker-enroll"');
    expect(scanner).toContain('class="bdna-worker-enroll"');
    expect(scanner).toContain("Đăng ký worker");
  });
});
