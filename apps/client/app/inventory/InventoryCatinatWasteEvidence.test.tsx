import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {afterEach,describe,expect,it,vi} from "vitest";
import {catinatWasteEvidenceApi} from "./api";
import {InventoryCatinatWasteEvidence} from "./InventoryCatinatWasteEvidence";

describe("Catinat witnessed disposal and manager countersign",()=>{
  afterEach(()=>vi.unstubAllGlobals());
  it("renders a two-person workflow but never a stock-posting action",()=>{
    const html=renderToStaticMarkup(createElement(InventoryCatinatWasteEvidence,{
      businessDate:"2026-10-20",sourceHash:"a".repeat(64),
    }));
    expect(html).toContain("người chứng kiến");
    expect(html).toContain("quản lý ký");
    expect(html).toContain("chưa trừ tồn");
    expect(html).not.toContain("Đã ghi sổ thành công");
  });

  it("posts a source-bound physical witness without writing stock",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({ok:true,json:async()=>({
      status:"staff_witnessed_unposted",physical_evidence_countersigned:false,
      ledger_posted:false,sheets_writes:0,
    })}));
    vi.stubGlobal("fetch",fetchMock);
    const body={
      approval_request_id:"approved-id",business_date:"2026-10-20",
      expected_source_hash:"a".repeat(64),
      idempotency_key:"physical-waste-000001",quantity:"1.5",
      observed_at:"2026-10-20T08:30:00.000Z",
      method:"Đã hủy và lập biên bản",evidence_reference:"document-drive-file-12345",
      note:"Chứng kiến hủy nguyên liệu trong ca",
    };
    const result=await catinatWasteEvidenceApi.witness(body);
    expect(result.ledger_posted).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/waste-evidence/witness"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(body);
  });

  it("manager countersign does not activate stock/Sheets writes",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({ok:true,json:async()=>({
      physical_evidence_countersigned:true,manager_certified_by:"manager-b",
      ledger_posted:false,ready_to_sign:false,inventory_writes:0,sheets_writes:0,
    })}));
    vi.stubGlobal("fetch",fetchMock);
    const result=await catinatWasteEvidenceApi.countersign("witness-id",{
      business_date:"2026-10-20",expected_source_hash:"a".repeat(64),
      note:"Đã xem chứng từ và đối chiếu nhân viên",
    });
    expect(result.ledger_posted).toBe(false);
    expect(result.ready_to_sign).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/waste-evidence/witness-id/countersign"
    );
  });
});
