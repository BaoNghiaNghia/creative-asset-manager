import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BrandingDnaPage, brandingDnaTabForPath } from "./BrandingDnaPage";

describe("Branding DNA Stage 0 preview", () => {
  it("maps supported nested routes without letting arbitrary URL text enter the iframe", () => {
    expect(brandingDnaTabForPath("/branding-dna")).toBe("overview");
    expect(brandingDnaTabForPath("/branding-dna/shops")).toBe("shops");
    expect(brandingDnaTabForPath("/branding-dna/scanner")).toBe("scanner");
    expect(brandingDnaTabForPath("/branding-dna/products")).toBe("products");
    expect(brandingDnaTabForPath("/branding-dna/dna")).toBe("dna");
    expect(brandingDnaTabForPath("/branding-dna/evil?next=https://attacker.test")).toBe("overview");
  });

  it("isolates sample actions and does not grant mock access to same-origin CAM cookies", () => {
    const html = renderToStaticMarkup(<BrandingDnaPage />);
    expect(html).toContain("dữ liệu mô phỏng");
    expect(html).toContain("chưa kết nối tài khoản shop");
    expect(html).toContain("sandbox=\"allow-scripts allow-downloads\"");
    expect(html).not.toContain("allow-same-origin");
    expect(html).not.toContain("allow-forms");
    expect(html).toContain("/branding-dna/index.html?embed=1");
  });
});
