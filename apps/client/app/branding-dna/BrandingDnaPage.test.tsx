import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BrandingDnaPage, brandingDnaTabForPath } from "./BrandingDnaPage";

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
    expect(html).toContain("Worker control plane");
    expect(html).toContain("Shop Registry và quản lý Worker");
    expect(html).toContain("Dedicated Windows Worker");
    expect(html).not.toContain("<iframe");
    expect(html).toContain('target="_blank"');
    expect(html).toContain("/branding-dna-preview/index.html?view=overview");
    expect(html).toContain("Scanner disabled");
  });
});
