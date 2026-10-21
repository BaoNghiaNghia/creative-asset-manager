import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { catinatApprovalApi, catinatCatalogCertificationApi, type CatinatApproval } from "./api";
import { InventoryCatinatApprovals, parseCatinatProposal, exactCatinatCertificationCandidates } from "./InventoryCatinatApprovals";

describe("Catinat manager review UI", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("requires a JSON object with actual evidence and rejects arbitrary text", () => {
    expect(parseCatinatProposal('{"material_id":"milk","counted":"6"}'))
      .toEqual({material_id:"milk",counted:"6"});
    for (const value of ['{}', '[]', 'null', '"6"', '6', '{wrong}']) {
      expect(() => parseCatinatProposal(value)).toThrow();
    }
  });

  it("shows safe workflow messaging and no direct Sheet write buttons", () => {
    const html = renderToStaticMarkup(createElement(InventoryCatinatApprovals));
    expect(html).toContain("Ngoại lệ kiểm kê Catinat");
    expect(html).toContain("Phê duyệt không tự ghi hay điều chỉnh Google Sheets.");
    expect(html).toContain("Gửi quản lý duyệt");
    expect(html).not.toContain("Tự chốt ca");
  });

  it("requires approved exact candidates and records evidence only",async()=>{
    const request = {
      kind:"catalog_mapping",status:"approved",
      proposal:{
        matching_status:"exact_name_and_unit_candidate",
        candidate_item_ids:["milk-a"],candidate_location_ids:["bar-a"],
      },
    } as unknown as CatinatApproval;
    expect(exactCatinatCertificationCandidates(request))
      .toEqual({item_id:"milk-a",location_id:"bar-a"});
    expect(exactCatinatCertificationCandidates({...request,status:"pending"})).toBeNull();
    expect(exactCatinatCertificationCandidates({
      ...request,proposal:{...request.proposal,candidate_item_ids:["milk-a","milk-b"]},
    })).toBeNull();
    const fake=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({mapping_activated:false,ready_to_sign:false,sheets_writes:0,items:[]}),
    }));
    vi.stubGlobal("fetch",fake);
    await catinatCatalogCertificationApi.list("2026-10-20");
    await catinatCatalogCertificationApi.certify({
      approval_request_id:"request-1",item_id:"milk-a",location_id:"bar-a",
      note:"Verified the unit and physical warehouse",
    });
    expect(fake.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/catalog-certifications?business_date=2026-10-20"
    );
    expect(fake.mock.calls[1][0]).toBe(
      "/api/inventory/daily-sheet/catinat/catalog-certifications"
    );
    expect(fake.mock.calls[1][1]?.method).toBe("POST");
    expect(JSON.parse(String(fake.mock.calls[1][1]?.body))).toEqual({
      approval_request_id:"request-1",item_id:"milk-a",location_id:"bar-a",
      note:"Verified the unit and physical warehouse",
    });
  });

  it("calls the tenant-scoped review API, not the Google Sheets writer", async () => {
    const fake = vi.fn(async (_url:string, _options?:RequestInit) => ({ok:true,json:async()=>({id:"request-1",status:"pending",items:[]})}));
    vi.stubGlobal("fetch",fake);
    await catinatApprovalApi.list("pending");
    await catinatApprovalApi.create({
      idempotency_key:"request-a1", business_date:"2026-10-20",
      kind:"discrepancy",subject_ref:"bar:milk",
      proposal:{counted:6},reason:"Missing two cans",
    });
    await catinatApprovalApi.decide("request-1","approved","Count checked by manager");
    const called = fake.mock.calls.map(args=>args[0]);
    expect(called).toEqual([
      "/api/inventory/daily-sheet/catinat/approvals?status=pending",
      "/api/inventory/daily-sheet/catinat/approvals",
      "/api/inventory/daily-sheet/catinat/approvals/request-1/decision",
    ]);
    expect(fake.mock.calls[2][1]?.method).toBe("POST");
    expect(JSON.parse(String(fake.mock.calls[2][1]?.body)))
      .toEqual({decision:"approved",reason:"Count checked by manager"});
  });
});
