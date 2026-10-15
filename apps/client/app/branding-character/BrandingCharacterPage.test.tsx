import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BrandingCharacterPage, brandingCharacterTabForPath } from "./BrandingCharacterPage";

describe("Branding Character Stage 0 preview", () => {
  it("maps supported nested routes without letting arbitrary URL text enter the iframe", () => {
    expect(brandingCharacterTabForPath("/branding-character")).toBe("overview");
    expect(brandingCharacterTabForPath("/branding-character/shops")).toBe("shops");
    expect(brandingCharacterTabForPath("/branding-character/scanner")).toBe("scanner");
    expect(brandingCharacterTabForPath("/branding-character/products")).toBe("products");
    expect(brandingCharacterTabForPath("/branding-character/dna")).toBe("dna");
    expect(brandingCharacterTabForPath("/branding-character/evil?next=https://attacker.test")).toBe("overview");
  });

  it("isolates sample actions and does not grant mock access to same-origin CAM cookies", () => {
    const html = renderToStaticMarkup(<BrandingCharacterPage />);
    expect(html).toContain("dữ liệu mô phỏng");
    expect(html).toContain("chưa kết nối tài khoản shop");
    expect(html).toContain("sandbox=\"allow-scripts allow-downloads\"");
    expect(html).not.toContain("allow-same-origin");
    expect(html).not.toContain("allow-forms");
    expect(html).toContain("/branding-character/index.html?embed=1");
  });
});
