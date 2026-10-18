import {describe,it,expect} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {BrandingDnaScopePreview} from "./BrandingDnaScopePreview";

describe("Branding DNA shop URL scope preview",()=>{
 it("offers only read-only validation and never a scanner execution control",()=>{
  const html=renderToStaticMarkup(<BrandingDnaScopePreview shopId="shop-1"/>);
  expect(html).toContain("Kiểm tra phạm vi URL");
  expect(html).toContain("Không truy cập URL");
  expect(html).toContain("Chưa có staged task");
  expect(html).not.toContain("Run Scan");
  expect(html).not.toContain("Bắt đầu quét");
  expect(html).not.toContain("Amazon credentials");
 });
});
