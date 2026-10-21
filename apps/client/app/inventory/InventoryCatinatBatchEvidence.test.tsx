import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {afterEach,describe,expect,it,vi} from "vitest";
import {catinatBatchApi} from "./api";
import {catinatVietnamInstant,InventoryCatinatBatchEvidence} from "./InventoryCatinatBatchEvidence";

describe("Catinat staff batch and disposal evidence",()=>{
  afterEach(()=>vi.unstubAllGlobals());

  it("converts exact Vietnam event hour to timezone-aware UTC, never date-only",()=>{
    expect(catinatVietnamInstant("2026-10-20T14:30")).toBe("2026-10-20T07:30:00.000Z");
    expect(()=>catinatVietnamInstant("2026-10-20")).toThrow("ngày và giờ");
    expect(()=>catinatVietnamInstant("2026-02-30T13:00")).toThrow("không hợp lệ");
  });

  it("renders staff attestation and disposal review without an apply/close button",()=>{
    const html=renderToStaticMarkup(createElement(InventoryCatinatBatchEvidence,{
      businessDate:"2026-10-20",sourceHash:"a".repeat(64),
      items:[{sheet:"KHO PHA CHẾ",cell:"L5",material:"Sữa đặc",unit:"lon",
        form:"whole",row:5,raw:"2",quantity:"2",status:"unconfirmed",
        blocker:"employee_physical_confirmation_required"}],
    }));
    expect(html).toContain("Nhập mã trên nhãn lô");
    expect(html).toContain("UTC+07:00");
    expect(html).toContain("Gửi đề nghị hủy — chưa trừ tồn");
    expect(html).not.toContain("Đã hủy hàng");
    expect(html).not.toContain("Đóng ca thành công");
  });

  it("calls source-bound staff batch route only",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({id:"id",status:"staff_attested_unverified",
        expiry_certified:false,disposal_performed:false,inventory_writes:0,sheets_writes:0}),
    }));
    vi.stubGlobal("fetch",fetchMock);
    const payload={
      idempotency_key:"batch-key-1234",business_date:"2026-10-20",
      expected_source_hash:"a".repeat(64),sheet:"KHO PHA CHẾ",cell:"L5",
      lot_reference:"LOT-1920",event_kind:"prepared" as const,
      event_at:"2026-10-20T07:30:00.000Z",package_expiry_at:null,
      note:"Nhân viên xác nhận lô từ nhãn thực tế",
    };
    const res=await catinatBatchApi.record(payload);
    expect(res.expiry_certified).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/inventory/daily-sheet/catinat/batch-evidence");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(payload);
  });

  it("submits disposal as manager approval proposal, not as a stock write",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({kind:"waste_disposal",status:"pending",
        inventory_write_performed:false,proposal:{ledger_posted:false}}),
    }));
    vi.stubGlobal("fetch",fetchMock);
    const req={
      business_date:"2026-10-20",expected_source_hash:"a".repeat(64),
      idempotency_key:"waste-key-1234",quantity:"1.5",
      reason:"Yêu cầu quản lý duyệt lô cần hủy",
    };
    const res=await catinatBatchApi.requestDisposal("lot-proof-id",req);
    expect(res.kind).toBe("waste_disposal");
    expect(res.inventory_write_performed).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/batch-evidence/lot-proof-id/request-disposal"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(req);
  });
});
