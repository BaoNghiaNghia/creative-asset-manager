import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BrandingDnaPage, brandingDnaTabForPath } from "./BrandingDnaPage";
import { BrandingDnaFoundation } from "./BrandingDnaFoundation";
import { BrandingDnaWorkers } from "./BrandingDnaWorkers";
import { BrandingDnaEvidence } from "./BrandingDnaEvidence";
import { BrandingDnaBrowserPlan } from "./BrandingDnaBrowserPlan";
import { BrandingDnaHandoffReview } from "./BrandingDnaHandoffReview";

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
    expect(library).toContain('<button type="button">+ Thêm shop</button>');
    expect(library).toContain("Shop Registry");
  });

  it("renders a real evidence reader without mock listing cards or scan controls", () => {
    const html = renderToStaticMarkup(<BrandingDnaEvidence />);
    expect(html).toContain("Product Explorer · Evidence");
    expect(html).toContain("Đang tải danh sách shop");
    expect(html).toContain("scanner vẫn tắt");
    expect(html).not.toContain("Start Scan");
    expect(html).not.toContain("<img");
  });

  it("renders Amazon/Etsy browser agent plan, not CSV/API scrape controls", () => {
    const etsy = renderToStaticMarkup(<BrandingDnaBrowserPlan shopId="sample-shop" platform="etsy"/>);
    expect(etsy).toContain("Browser Agent · Etsy");
    const html = renderToStaticMarkup(<BrandingDnaBrowserPlan shopId="sample-shop" platform="amazon"/>);
    expect(html).toContain("Browser Agent · Amazon");
    expect(html).toContain("Chưa bật quét");
    expect(html).not.toContain('type="file"');
    expect(html).not.toContain("Start Scan");
    expect(html).not.toContain("<img");
  });

  it("renders tenant-bound handoff staging without any auto-approval or scan action", () => {
    const html = renderToStaticMarkup(<BrandingDnaHandoffReview shopId="shop-1" platform="etsy" accessMode="authorized" />);
    expect(html).toContain("Handoff · Kiểm chứng nguồn");
    expect(html).toContain('type="file"');
    expect(html).toContain("Stage");
    expect(html).toContain("Verification Queue");
    expect(html).toContain("handoff-status-filter");
    expect(html).toContain("Chờ kiểm chứng");
    expect(html).toContain("Cần nguồn chính thức");
    expect(html).not.toContain("Verified</button>");
    expect(html).not.toContain("Start Scan");
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
