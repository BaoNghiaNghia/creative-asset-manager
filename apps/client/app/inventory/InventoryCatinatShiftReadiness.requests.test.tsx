// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InventoryCatinatShiftReadiness } from "./InventoryCatinatShiftReadiness";
import { catinatShiftApi, catinatApprovalApi, InventoryApiError, type CatinatEveningReadiness, type CatinatCatalogMapping } from "./api";

vi.mock("./InventoryCatinatBatchEvidence", () => ({ InventoryCatinatBatchEvidence: () => null }));
vi.mock("./InventoryCatinatWasteEvidence", () => ({ InventoryCatinatWasteEvidence: () => null }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sourceHash = "a".repeat(64);
let root: Root | null = null;
let container: HTMLDivElement | null = null;

function readiness(day: string, material = "SỮA ĐẶC"): CatinatEveningReadiness {
  return {
    business_date: day, source_hash: sourceHash, shift: "evening",
    mode: "read_only_not_a_signed_close", staff_confirmation_available: false,
    automatic_carry_forward_allowed: false, sheets_writes: 0,
    items: [{ sheet: "KHO PHA CHẾ", row: 5, cell: "L5", form: "whole", unit: "lon",
      raw: "2", quantity: "2", status: "unconfirmed",
      blocker: "employee_physical_confirmation_required", material }],
    blockers: [{ code: "employee_physical_confirmation_required", sheet: "KHO PHA CHẾ", cell: "L5" }],
  };
}

beforeEach(() => {
  vi.spyOn(catinatShiftApi, "readiness").mockImplementation(async day => readiness(day));
  vi.spyOn(catinatShiftApi, "confirmations").mockImplementation(async day => ({
    business_date: day, source_hash: sourceHash, source_status: "current",
    confirmed_count: 0, required_count: 1,
    cells: [{ sheet: "KHO PHA CHẾ", cell: "L5", material: "SỮA ĐẶC", unit: "lon",
      quantity: "2", confirmed: false, confirmed_by: null }],
    official_shift_closed: false, automatic_carry_forward_allowed: false, sheets_writes: 0,
  }));
  vi.spyOn(catinatShiftApi, "closingPreflight").mockImplementation(async day => ({
    business_date: day, source_hash: sourceHash, shift: "evening",
    confirmed_count: 0, required_count: 1, outstanding_transfer_count: 0,
    unreconciled_transfer_count: 0, approval_requests_count: 0,
    identity_source: "q01_q47_configured_worksheet_and_closing_cell",
    warehouse_sources: [{ sheet: "KHO PHA CHẾ", whole_column: "L", loose_column: "M", required_cells: 1, confirmed_cells: 0 }],
    physical_identity_verified_for_preview: true,
    blockers: [{ code: "physical_count_unconfirmed_or_stale" }],
    ready_to_sign: false, official_shift_closed: false,
    automatic_carry_forward_allowed: false, sheets_writes: 0, mode: "read_only_safety_preflight",
  }));
  vi.spyOn(catinatShiftApi, "catalogMappingPreview").mockImplementation(async day => ({
    business_date: day, source_hash: sourceHash, shift: "evening",
    mode: "read_only_uncertified_catalog_candidates",
    catalog_coverage: { active_item_count: 0, active_location_count: 0, certified: false },
    locations: [], items: [], summary: { total: 0, exact_candidates: 0, requires_review: 0, by_status: {} },
    ready_to_sign: false, automatic_carry_forward_allowed: false, inventory_writes: 0, sheets_writes: 0,
  }));
  vi.spyOn(catinatShiftApi, "certificationCoverage").mockImplementation(async day => ({
    business_date: day, source_hash: sourceHash, mode: "read_only_certification_coverage",
    areas: [], items: [], summary: { source_cells: 1, evidence_cells: 0, pending_cells: 1,
      warehouse_areas_with_exact_candidate: 0, total_warehouse_areas: 3 },
    mapping_activated: false, ready_to_sign: false, automatic_carry_forward_allowed: false,
    inventory_writes: 0, sheets_writes: 0,
  }));
  vi.spyOn(catinatShiftApi, "packageConversionReview").mockImplementation(async day => ({
    business_date: day, source_hash: sourceHash, mode: "read_only_package_conversion_evidence",
    items: [], summary: { total_cells: 1, by_status: {}, approved_conversion_evidence_cells: 0 },
    conversion_applied: false, ready_to_sign: false, automatic_carry_forward_allowed: false,
    inventory_writes: 0, sheets_writes: 0,
  }));
  vi.spyOn(catinatShiftApi, "safetyTriage").mockImplementation(async day => ({
    business_date: day, source_hash: sourceHash, mode: "read_only_shelf_life_waste_transfer_triage",
    prepared_report_hints: [], items: [], transfers: [],
    summary: { physical_cells_without_batch_expiry_certification: 1,
      date_only_preparation_hints: 0, pending_receipt_transfers: 0, unposted_ledger_transfers: 0 },
    batch_expiration_and_waste_certified: false, transfer_ledger_reconciled: false,
    ready_to_sign: false, automatic_carry_forward_allowed: false, inventory_writes: 0, sheets_writes: 0,
  }));
  vi.spyOn(catinatShiftApi, "confirmCounts").mockImplementation(async request => ({
    business_date: request.business_date, source_hash: request.expected_source_hash,
    confirmed: [], official_shift_closed: false, inventory_write_performed: false, sheets_writes: 0,
  }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.restoreAllMocks();
});

async function mount() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(<InventoryCatinatShiftReadiness />); });
}
const button = (label: string) => {
  const element = [...container!.querySelectorAll("button")].find(item => item.textContent?.includes(label));
  expect(element).toBeDefined();
  return element!;
};
const count = () => container!.querySelector<HTMLInputElement>('input[aria-label^="Xác nhận đã kiểm đếm"]');
const dayInput = () => container!.querySelector<HTMLInputElement>("#catinat-shift-day")!;
const load = async () => { await act(async () => { button("Kiểm tra số liệu Google Sheets").click(); }); };

describe("Catinat physical-count request isolation", () => {
  it("keeps physical counting usable when optional catalog diagnostics fail", async () => {
    vi.mocked(catinatShiftApi.certificationCoverage).mockRejectedValue(
      new InventoryApiError(503, "Optional certification store unavailable"),
    );
    await mount();
    const day = dayInput().value;
    await load();
    expect(container!.textContent).toContain("Chưa tải được báo cáo chứng cứ liên kết danh mục");
    expect(count()?.disabled).toBe(false);
    expect(catinatShiftApi.confirmCounts).not.toHaveBeenCalled();
    await act(async () => { count()!.click(); });
    await act(async () => { container!.querySelector<HTMLInputElement>("#catinat-confirm-physical")!.click(); });
    await act(async () => { button("Lưu xác nhận kiểm đếm").click(); });
    expect(catinatShiftApi.confirmCounts).toHaveBeenCalledTimes(1);
    expect(catinatShiftApi.confirmCounts).toHaveBeenCalledWith({
      business_date: day, expected_source_hash: sourceHash,
      cells: [{ sheet: "KHO PHA CHẾ", cell: "L5" }],
    });
    expect(container!.textContent).toContain("Ca tối CHƯA được chốt");
  });

  it("fails closed when required count evidence cannot be read", async () => {
    vi.mocked(catinatShiftApi.confirmations).mockRejectedValue(new Error("Count evidence unavailable"));
    await mount();
    await load();
    expect(container!.textContent).toContain("Count evidence unavailable");
    expect(count()).toBeNull();
    expect(catinatShiftApi.confirmCounts).not.toHaveBeenCalled();
  });

  it("rejects mixed source hashes even from a supplemental report", async () => {
    const original = vi.mocked(catinatShiftApi.catalogMappingPreview).getMockImplementation()!;
    vi.mocked(catinatShiftApi.catalogMappingPreview).mockImplementation(async day => ({
      ...await original(day), source_hash: "b".repeat(64),
    }));
    await mount();
    await load();
    expect(container!.textContent).toContain("thay đổi trong lúc kiểm tra");
    expect(count()).toBeNull();
    expect(catinatShiftApi.confirmCounts).not.toHaveBeenCalled();
  });

  it("rejects a response for a different requested business date", async () => {
    vi.mocked(catinatShiftApi.readiness).mockResolvedValue(readiness("2000-01-01"));
    await mount();
    await load();
    expect(container!.textContent).toContain("Ngày kiểm kê");
    expect(container!.textContent).toContain("thay đổi trong lúc kiểm tra");
    expect(count()).toBeNull();
  });

  it.each(["source_hash", "business_date", "catalog_only"] as const)(
    "invalidates physical evidence only when mapping refresh changes %s", async change => {
      const original = vi.mocked(catinatShiftApi.catalogMappingPreview).getMockImplementation()!;
      let reads = 0;
      vi.mocked(catinatShiftApi.catalogMappingPreview).mockImplementation(async day => {
        const report: CatinatCatalogMapping = {
          ...await original(day),
          locations: [{ sheet: "KHO PHA CHẾ", status: "exact_name_candidate", candidate_location_ids: ["location-a"] }],
          items: [{ sheet: "KHO PHA CHẾ", cell: "L5", material: "SỮA ĐẶC",
            form: "whole", unit: "lon", status: "exact_name_and_unit_candidate",
            candidate_item_ids: ["item-a"], location_status: "exact_name_candidate" }],
          summary: { total: 1, exact_candidates: 1, requires_review: 0, by_status: {} },
        };
        if (++reads > 1) {
          if (change === "source_hash") report.source_hash = "b".repeat(64);
          else if (change === "business_date") report.business_date = "2000-01-01";
          else report.items[0].candidate_item_ids = ["item-b"];
        }
        return report;
      });
      const create = vi.spyOn(catinatApprovalApi, "create").mockRejectedValue(new Error("Unexpected approval request"));
      await mount();
      await load();
      await act(async () => { count()!.click(); });
      await act(async () => { container!.querySelector<HTMLInputElement>("#catinat-confirm-physical")!.click(); });
      expect(button("Lưu xác nhận kiểm đếm").disabled).toBe(false);
      await act(async () => { button("Đề nghị xác minh").click(); });
      await act(async () => {
        const reason = container!.querySelector<HTMLTextAreaElement>("#catinat-mapping-reason")!;
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(reason, "Đối chiếu bằng chứng danh mục");
        reason.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => { button("Gửi yêu cầu xác minh").click(); });
      expect(catinatShiftApi.catalogMappingPreview).toHaveBeenCalledTimes(2);
      expect(container!.textContent).toContain("Dữ liệu Sheets/danh mục đã thay đổi");
      expect(create).not.toHaveBeenCalled();
      expect(catinatShiftApi.confirmCounts).not.toHaveBeenCalled();
      if (change === "catalog_only") {
        expect(count()?.checked).toBe(true);
        expect(button("Lưu xác nhận kiểm đếm").disabled).toBe(false);
      } else {
        expect(count()).toBeNull();
        expect(container!.querySelector("#catinat-confirm-physical")).toBeNull();
        expect(container!.textContent).not.toContain("Đã xác nhận 0/1 ô tồn");
      }
    },
  );

  it("locks date changes during requests and discards an obsolete response", async () => {
    let resolveOld!: (value: CatinatEveningReadiness) => void;
    vi.mocked(catinatShiftApi.readiness).mockImplementationOnce(
      () => new Promise(resolve => { resolveOld = resolve; }),
    );
    await mount();
    const oldDay = dayInput().value;
    await load();
    expect(dayInput().disabled).toBe(true);
    // Also cover an external controlled-date update while a request is pending.
    const nextDay = oldDay === "2026-10-20" ? "2026-10-19" : "2026-10-20";
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(dayInput(), nextDay);
      dayInput().dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(dayInput().value).toBe(nextDay);
    expect(dayInput().disabled).toBe(false);
    await load();
    expect(count()).not.toBeNull();
    await act(async () => { resolveOld(readiness(oldDay, "OBSOLETE MATERIAL")); });
    expect(container!.textContent).not.toContain("OBSOLETE MATERIAL");
    expect(dayInput().value).toBe(nextDay);
    expect(dayInput().disabled).toBe(false);
    expect(catinatShiftApi.confirmCounts).not.toHaveBeenCalled();
  });
});
