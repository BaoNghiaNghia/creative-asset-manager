import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {afterEach,describe,expect,it,vi} from "vitest";
import {catinatShiftApi,type CatinatPhysicalReadinessItem} from "./api";
import {InventoryCatinatShiftReadiness,summarizeCatinatReadiness} from "./InventoryCatinatShiftReadiness";

describe("Catinat shift readiness",()=>{
  afterEach(()=>vi.unstubAllGlobals());

  it("counts verified-looking numbers as present but never asserts staff confirmation",()=>{
    const make=(sheet:string,cell:string,quantity:string|null):CatinatPhysicalReadinessItem=>({
      sheet,cell,quantity,material:"SỮA ĐẶC",form:"whole",row:5,unit:"lon",
      raw:quantity??"",status:"unconfirmed",
      blocker:quantity?"employee_physical_confirmation_required":"physical_count_missing_or_ambiguous",
    });
    const results=summarizeCatinatReadiness([
      make("KHO PHA CHẾ","L5",null),make("PHÒNG PHA CHẾ","H5","0"),
      make("PHÒNG PHA CHẾ","H6","6"),
    ]);
    expect(results["KHO PHA CHẾ"]).toEqual({total:1,missing:1,present:0});
    expect(results["PHÒNG PHA CHẾ"]).toEqual({total:2,missing:0,present:2});
  });

  it("only renders a read-only preflight action, not a premature close button",()=>{
    const markup=renderToStaticMarkup(createElement(InventoryCatinatShiftReadiness));
    expect(markup).toContain("Kiểm kê ca tối");
    expect(markup).toContain("Kiểm tra số liệu Google Sheets");
    expect(markup).toContain("không chốt ca");
    expect(markup).not.toContain("Hoàn tất kiểm kê ca tối");
  });

  it("uses the protected inventory-read API rather than a writer endpoint",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({items:[],blockers:[],mode:"read_only_not_a_signed_close"}),
    }));
    vi.stubGlobal("fetch",fetchMock);
    await catinatShiftApi.readiness("2026-10-20");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/shift/readiness?business_date=2026-10-20"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
  });
});
