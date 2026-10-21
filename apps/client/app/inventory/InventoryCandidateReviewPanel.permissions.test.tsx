// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InventoryCandidateReviewPanel } from "./InventoryCandidateReviewPanel";
import { catinatMaterialGroupsApi, InventoryApiError, type CatinatMaterialGroupsResponse } from "./api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
let container: HTMLDivElement | null = null;
const evidenceHash = "e".repeat(64);

function report(canApprove: boolean, count = 1): CatinatMaterialGroupsResponse {
  return {
    source_rows: count, unique_names: count, can_approve_candidates: canApprove,
    automatic_approvals: 0, inventory_writes: 0, sheet_writes: 0,
    groups: Array.from({ length: count }, (_, index) => ({
      name: "Material " + index, aliases: [], source_row_count: 1, sheets: ["KHO PHA CHẾ"],
      evidence_units: ["lon"], status: "exact_name_and_evidence_unit_candidate",
      suggested_item_id: "item-" + index, suggested_item_name: "Material " + index,
      candidate_item_ids: ["item-" + index], certified: false, source_rows: [],
      approval_groups: [{
        candidate_id: "candidate-" + index, candidate_ids: ["candidate-" + index],
        expected_count: 1, sheet: "KHO PHA CHẾ", category: null, name: "Material " + index,
        item_id: "item-" + index, item_name: "Material " + index, item_units: ["lon"],
        can_bulk_approve: true, blocker: null, evidence_cells: ["C5"], evidence_hash: evidenceHash,
      }],
    })),
  };
}

const button = (text: string) => {
  const result = [...container!.querySelectorAll("button")].find(item => item.textContent?.includes(text));
  expect(result).toBeDefined();
  return result!;
};
const firstCheckbox = () => container!.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
async function mount() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(<InventoryCandidateReviewPanel />); });
}
afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  container?.remove();
  container = null;
  vi.restoreAllMocks();
});

describe("Catinat material approval capability and evidence", () => {
  it("keeps staff preview readable while preventing manager-only approval", async () => {
    vi.spyOn(catinatMaterialGroupsApi, "list").mockResolvedValue(report(false));
    const approve = vi.spyOn(catinatMaterialGroupsApi, "bulkApprove");
    await mount();
    expect(container!.textContent).toContain("Material 0");
    expect(container!.textContent).toContain("Chỉ quản lý có quyền phê duyệt nguyên liệu");
    expect(firstCheckbox().disabled).toBe(true);
    expect(button("Duyệt nhóm đã chọn").disabled).toBe(true);
    await act(async () => { button("Duyệt nhóm đã chọn").click(); });
    expect(approve).not.toHaveBeenCalled();
  });

  it("sends the exact reviewed evidence hash only after explicit selection", async () => {
    vi.spyOn(catinatMaterialGroupsApi, "list").mockResolvedValue(report(true));
    const approve = vi.spyOn(catinatMaterialGroupsApi, "bulkApprove").mockResolvedValue({
      status: "approved", approved_groups: 1, approved_candidates: 1,
      inventory_balance_writes: 0, google_sheet_writes: 0,
    });
    await mount();
    expect(approve).not.toHaveBeenCalled();
    await act(async () => { firstCheckbox().click(); });
    await act(async () => { button("Duyệt nhóm đã chọn").click(); });
    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve).toHaveBeenCalledWith({ groups: [{
      candidate_id: "candidate-0", item_id: "item-0", expected_count: 1,
      expected_candidate_ids: ["candidate-0"], expected_evidence_hash: evidenceHash,
    }] });
    expect(firstCheckbox().checked).toBe(false);
  });

  it("requires a fresh selection after refreshing the preview", async () => {
    vi.spyOn(catinatMaterialGroupsApi, "list").mockResolvedValue(report(true));
    const approve = vi.spyOn(catinatMaterialGroupsApi, "bulkApprove");
    await mount();
    await act(async () => { firstCheckbox().click(); });
    expect(firstCheckbox().checked).toBe(true);
    await act(async () => { button("Làm mới").click(); });
    expect(firstCheckbox().checked).toBe(false);
    expect(button("Duyệt nhóm đã chọn").disabled).toBe(true);
    expect(approve).not.toHaveBeenCalled();
  });

  it("caps a single approval at the server limit of 50 groups", async () => {
    vi.spyOn(catinatMaterialGroupsApi, "list").mockResolvedValue(report(true, 51));
    await mount();
    await act(async () => { button("Chọn 50 nhóm").click(); });
    const checkboxes = [...container!.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(checkboxes.filter(item => item.checked)).toHaveLength(50);
    expect(checkboxes[50].disabled).toBe(true);
    expect(container!.textContent).toContain("Mỗi lần duyệt tối đa 50 nhóm");
  });

  it("discards stale evidence instead of retrying an outdated approval", async () => {
    vi.spyOn(catinatMaterialGroupsApi, "list").mockResolvedValue(report(true));
    const approve = vi.spyOn(catinatMaterialGroupsApi, "bulkApprove").mockRejectedValue(
      new InventoryApiError(409, "Stale evidence", { code: "candidate_group_evidence_changed" }),
    );
    await mount();
    await act(async () => { firstCheckbox().click(); });
    await act(async () => { button("Duyệt nhóm đã chọn").click(); });
    expect(container!.textContent).toContain("Bằng chứng nguyên liệu đã thay đổi");
    expect(button("Duyệt nhóm đã chọn").disabled).toBe(true);
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it("handles a revoked manager permission without keeping an enabled action", async () => {
    vi.spyOn(catinatMaterialGroupsApi, "list").mockResolvedValue(report(true));
    vi.spyOn(catinatMaterialGroupsApi, "bulkApprove").mockRejectedValue(
      new InventoryApiError(403, "Permission required"),
    );
    await mount();
    await act(async () => { firstCheckbox().click(); });
    await act(async () => { button("Duyệt nhóm đã chọn").click(); });
    expect(container!.textContent).toContain("Tài khoản cần quyền quản lý nguyên liệu");
    expect(firstCheckbox().disabled).toBe(true);
    expect(button("Duyệt nhóm đã chọn").disabled).toBe(true);
  });
});
