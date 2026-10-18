import {describe,expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {BrandingDnaReadiness} from "./BrandingDnaReadiness";

describe("Brand DNA source readiness",()=>{
 it("shows evidence-only readiness without any inference or generation controls",()=>{
   const html=renderToStaticMarkup(<BrandingDnaReadiness/>);
   expect(html).toContain("Brand DNA · Input Readiness");
   expect(html).toContain("Chỉ đọc dữ liệu");
   expect(html).toContain('aria-label="Shop for DNA readiness"');
   expect(html).not.toContain("Generate DNA</button>");
   expect(html).not.toContain("Đã duyệt DNA");
 });
});
