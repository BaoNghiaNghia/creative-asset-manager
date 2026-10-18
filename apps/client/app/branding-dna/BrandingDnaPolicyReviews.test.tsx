import {describe,expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {BrandingDnaPolicyReviews} from "./BrandingDnaPolicyReviews";

describe("Shop Policy Review",()=>{
 it("keeps marketplace dispatch disabled and no job launch affordance",()=>{
  const html=renderToStaticMarkup(<BrandingDnaPolicyReviews/>);
  expect(html).toContain("Hồ sơ quyền truy cập shop");
  expect(html).toContain("Dispatch vẫn bị khóa");
  expect(html).toContain("Chọn shop");
  expect(html).not.toContain("Bắt đầu Scan");
  expect(html).not.toContain("Scan Now");
 });
});
