import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {afterEach,describe,expect,it,vi} from "vitest";
import {catinatTransferApi,catinatShiftApi} from "./api";
import {InventoryCatinatTransfers} from "./InventoryCatinatTransfers";

describe("Catinat transfer tracker",()=>{
  afterEach(()=>vi.unstubAllGlobals());

  it("renders separated handoff/receipt instead of automatic stock credit",()=>{
    const markup=renderToStaticMarkup(createElement(InventoryCatinatTransfers));
    expect(markup).toContain("Chuyển kho");
    expect(markup).toContain("chưa tự ghi Google Sheets");
    expect(markup).toContain("Lưu bàn giao, chờ bên nhận xác nhận");
    expect(markup).not.toContain("Tự chuyển tồn ngày mới");
  });

  it("uses staff-authenticated inventory tracker endpoints only",async()=>{
    const mocked=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({items:[],status:"pending_receipt",ledger_posted:false}),
    }));
    vi.stubGlobal("fetch",mocked);
    await catinatTransferApi.list(true);
    await catinatTransferApi.dispatch({
      reference_key:"dispatch-a1",business_date:"2026-10-20",item_id:"milk",
      source_location_id:"store",destination_location_id:"bar",unit:"lon",quantity:"6",
    });
    await catinatTransferApi.receive("handoff-1",{
      event_key:"receipt-a1",quantity:"3",
    });
    await catinatShiftApi.closingPreflight("2026-10-20");
    expect(mocked.mock.calls.map(x=>x[0])).toEqual([
      "/api/inventory/daily-sheet/catinat/transfers?pending_only=true",
      "/api/inventory/daily-sheet/catinat/transfers",
      "/api/inventory/daily-sheet/catinat/transfers/handoff-1/receive",
      "/api/inventory/daily-sheet/catinat/shift/closing-preflight?business_date=2026-10-20",
    ]);
    expect(mocked.mock.calls[1][1]?.method).toBe("POST");
    expect(mocked.mock.calls[2][1]?.method).toBe("POST");
    expect(mocked.mock.calls[3][1]?.method).toBeUndefined();
  });
});
