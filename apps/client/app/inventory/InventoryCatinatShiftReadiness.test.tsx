import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {afterEach,describe,expect,it,vi} from "vitest";
import {catinatShiftApi,type CatinatPhysicalReadinessItem,type CatinatCatalogMapping} from "./api";
import {InventoryCatinatShiftReadiness,summarizeCatinatReadiness,CATINAT_MAPPING_STATUS,buildCatinatMappingReviewProposal} from "./InventoryCatinatShiftReadiness";

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

  it("builds a source-bound manager review request with uncertified candidates only",()=>{
    const row={
      sheet:"KHO PHA CHẾ",cell:"L5",material:"SỮA ĐẶC",
      unit:"lon",form:"whole",status:"exact_name_and_unit_candidate",
      candidate_item_ids:["milk-a"],location_status:"exact_name_candidate",
    };
    const report:CatinatCatalogMapping={
      business_date:"2026-10-20",shift:"evening",source_hash:"a".repeat(64),
      mode:"read_only_uncertified_catalog_candidates",
      catalog_coverage:{active_item_count:14,active_location_count:1,certified:false},
      locations:[{sheet:"KHO PHA CHẾ",status:"exact_name_candidate",
                  candidate_location_ids:["bar-a"]}],
      items:[row],summary:{total:1,exact_candidates:1,requires_review:0,by_status:{}},
      ready_to_sign:false,automatic_carry_forward_allowed:false,
      inventory_writes:0,sheets_writes:0,
    };
    const evidence=buildCatinatMappingReviewProposal(report,row);
    expect(evidence).toMatchObject({
      source_hash:"a".repeat(64),source_sheet:"KHO PHA CHẾ",source_cell:"L5",
      material_display:"SỮA ĐẶC",source_form:"whole",source_unit:"lon",
      candidate_item_ids:["milk-a"],candidate_location_ids:["bar-a"],
      mapping_certified:false,inventory_write_performed:false,sheets_writes:0,
    });
    expect(evidence).not.toHaveProperty("approved_item_id");
    expect(()=>buildCatinatMappingReviewProposal(report,{...row,cell:"L6"}))
      .toThrow("không còn khớp");
  });

  it("only renders a read-only preflight action, not a premature close button",()=>{
    const markup=renderToStaticMarkup(createElement(InventoryCatinatShiftReadiness));
    expect(markup).toContain("Kiểm kê ca tối");
    expect(markup).toContain("Kiểm tra số liệu Google Sheets");
    expect(markup).toContain("không chốt ca");
    expect(markup).not.toContain("Hoàn tất kiểm kê ca tối");
  });

  it("uses explicit staff count confirmation API without a close or Sheets write",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({confirmed:[],official_shift_closed:false,sheets_writes:0}),
    }));
    vi.stubGlobal("fetch",fetchMock);
    await catinatShiftApi.confirmations("2026-10-20");
    await catinatShiftApi.confirmCounts({
      business_date:"2026-10-20",expected_source_hash:"a".repeat(64),
      cells:[{sheet:"KHO PHA CHẾ",cell:"L5"}],
    });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/shift/count-confirmations?business_date=2026-10-20"
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/inventory/daily-sheet/catinat/shift/count-confirmations"
    );
    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      business_date:"2026-10-20",expected_source_hash:"a".repeat(64),
      cells:[{sheet:"KHO PHA CHẾ",cell:"L5"}],
    });
  });

  it("reads package conversion evidence without applying it or closing inventory",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({
        business_date:"2026-10-20",source_hash:"a".repeat(64),
        mode:"read_only_package_conversion_evidence",
        summary:{total_cells:1,approved_conversion_evidence_cells:0,
          by_status:{approved_package_conversion_missing:1}},
        items:[],conversion_applied:false,ready_to_sign:false,
        automatic_carry_forward_allowed:false,
        inventory_writes:0,sheets_writes:0,
      }),
    }));
    vi.stubGlobal("fetch",fetchMock);
    const result=await catinatShiftApi.packageConversionReview("2026-10-20");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/catalog-certifications/conversion-review?business_date=2026-10-20"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    expect(result.conversion_applied).toBe(false);
    expect(result.sheets_writes).toBe(0);
    expect(result.summary.by_status.approved_package_conversion_missing).toBe(1);
  });

  it("reads manager evidence coverage without activating mapping or writing Sheets",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({
        business_date:"2026-10-20",source_hash:"a".repeat(64),
        mode:"read_only_certification_coverage",
        summary:{source_cells:1,evidence_cells:0,pending_cells:1,
          warehouse_areas_with_exact_candidate:0,total_warehouse_areas:3},
        areas:[],items:[],mapping_activated:false,ready_to_sign:false,
        automatic_carry_forward_allowed:false,inventory_writes:0,sheets_writes:0,
      }),
    }));
    vi.stubGlobal("fetch",fetchMock);
    const result=await catinatShiftApi.certificationCoverage("2026-10-20");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/catalog-certifications/coverage?business_date=2026-10-20"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    expect(result.summary.total_warehouse_areas).toBe(3);
    expect(result.mapping_activated).toBe(false);
    expect(result.ready_to_sign).toBe(false);
    expect(result.sheets_writes).toBe(0);
  });

  it("uses read-only tenant-scoped catalog mapping preview with Vietnamese review reasons",async()=>{
    const fetchMock=vi.fn(async (_url:string,_options?:RequestInit)=>({
      ok:true,json:async()=>({items:[],locations:[],ready_to_sign:false,sheets_writes:0}),
    }));
    vi.stubGlobal("fetch",fetchMock);
    await catinatShiftApi.catalogMappingPreview("2026-10-20");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/inventory/daily-sheet/catinat/catalog/mapping-preview?business_date=2026-10-20"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    expect(CATINAT_MAPPING_STATUS.ambiguous_item).toContain("trùng tên");
    expect(CATINAT_MAPPING_STATUS.exact_name_and_unit_candidate).toContain("chờ quản lý");
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
