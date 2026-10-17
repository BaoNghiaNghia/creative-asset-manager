import {describe,expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {BrandingDnaWorkers} from "./BrandingDnaWorkers";

describe("Branding DNA paired Scout status",()=>{
  it("distinguishes paired standby presence from dedicated worker registry",()=>{
    const html=renderToStaticMarkup(<BrandingDnaWorkers/>);
    expect(html).toContain("Scout Manager · CAM-paired Browser Agents");
    expect(html).toContain("Dispatch disabled");
    expect(html).toContain("Dedicated Windows Worker");
    expect(html).toContain("Scanner chưa cấp job");
    expect(html).not.toContain("Scan running");
  });
});
