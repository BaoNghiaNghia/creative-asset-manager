import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { InventoryDailyOverview } from "./InventoryDailyTab";
import type { InventoryDailySheetStatus } from "../inventory/api";

function status(): InventoryDailySheetStatus {
  return {
    enabled: true, configured: true, execution_mode: "v4_slots",
    operational_state: "healthy", image_pipeline_enabled: true,
    timezone: "Asia/Ho_Chi_Minh", current_local_date: "2026-10-21",
    working_business_date: "2026-10-21", as_of_business_date: "2026-10-21",
    snapshot_time: "17:00", reconcile_time: "22:00", carry_forward_time: "09:00",
    next_snapshot_at: "2026-10-21T17:00:00+07:00",
    next_reconciliation_at: "2026-10-21T22:00:00+07:00",
    next_carry_forward_at: "2026-10-22T09:00:00+07:00",
    working_spreadsheet_url: null, last_snapshot: null,
    last_reconciliation: null, carry_forward: null,
    lifecycle: {
      business_date: "2026-10-21",
      morning_reset: { status: "completed", scheduled_time: "09:00", source_business_date: "2026-10-20", source_gemini_file_id: null },
      afternoon_snapshot: { status: "completed", scheduled_time: "17:00", snapshot_file_id: "snapshot", gemini_file_id: "copy" },
      evening_reconcile: { status: "completed", scheduled_time: "22:00", verified: true, run_id: null, plan_hash: null, prompt_version: null, prompt_hash: null },
    },
  };
}

const render = (value: InventoryDailySheetStatus) => renderToStaticMarkup(
  <InventoryDailyOverview status={value} run={null} />,
);

describe("Inventory overview evidence states", () => {
  it.each(["morning_reset", "afternoon_snapshot", "evening_reconcile"] as const)(
    "shows the %s failure even without a legacy daily run", stage => {
      const value = status();
      value.lifecycle![stage].status = "failed";
      value.lifecycle![stage].error_code = "fixture_stage_failure";
      const html = render(value);
      expect(html).toContain("Cần kiểm tra chu kỳ hiện tại");
      expect(html).toContain("fixture_stage_failure");
      expect(html).toContain("Cần recovery");
      expect(html).not.toContain("Không phát hiện ngoại lệ");
      expect(html).not.toContain("<strong>Đã hoàn tất</strong>");
    },
  );

  it("does not claim zero invalid rows when reconciliation has no evidence", () => {
    const html = render(status());
    expect(html).toContain("Chưa có dữ liệu");
    expect(html).toContain("Chưa có kết quả đối soát");
    expect(html).not.toContain('ops-inventory-summary-card result success');
    expect(html).not.toContain("0 không hợp lệ");
  });

  it("keeps an incomplete healthy cycle in an unknown state", () => {
    const value = status();
    value.lifecycle!.evening_reconcile.status = "scheduled";
    value.lifecycle!.evening_reconcile.verified = false;
    const html = render(value);
    expect(html).toContain("Chưa đủ dữ liệu để kết luận");
    expect(html).not.toContain("Không phát hiện ngoại lệ");
  });

  it("does not treat legacy fallback records as current V4 lifecycle evidence", () => {
    const value = status();
    value.lifecycle = undefined;
    value.last_snapshot = {
      id: "historical-snapshot", business_date: "2026-10-20", status: "completed",
      snapshot_file_id: "old-snapshot", snapshot_url: null,
      gemini_file_id: "old-copy", gemini_url: null, archive_folder_url: null,
      error_code: null, completed_at: "2026-10-20T17:00:00+07:00",
    };
    value.last_reconciliation = {
      id: "historical-reconciliation", business_date: "2026-10-20",
      previous_business_date: "2026-10-19", status: "completed",
      summary: { row_count: 1, changed_count: 0, invalid_count: 0 },
      error_code: null, completed_at: "2026-10-20T22:00:00+07:00",
    };
    value.carry_forward = {
      status: "completed", target_business_date: "2026-10-20",
      previous_business_date: "2026-10-19", completed_at: "2026-10-20T09:00:00+07:00",
      material_count: 0, warehouse_count: 0, issue_count: 0, error_code: null,
    };
    const html = render(value);
    expect(html).toContain("Chưa đủ dữ liệu để kết luận");
    expect(html).not.toContain("<strong>Đã hoàn tất</strong>");
    expect(html).not.toContain("Không phát hiện ngoại lệ");
  });

  it("requires current-date complete stages and verified evening evidence", () => {
    expect(render(status())).toContain("Không phát hiện ngoại lệ");
    const value = status();
    value.lifecycle!.business_date = "2026-10-20";
    const html = render(value);
    expect(html).toContain("chưa khớp ngày kiểm kê");
    expect(html).not.toContain("Không phát hiện ngoại lệ");
  });

  it("surfaces server invariants and disabled automation", () => {
    const value = status();
    value.invariants = [{ code: "fixture_source_conflict", stage: "evening_reconcile" }];
    expect(render(value)).toContain("fixture_source_conflict");
    expect(render(value)).not.toContain("Không phát hiện ngoại lệ");
    value.invariants = [];
    value.enabled = false;
    expect(render(value)).toContain("Lịch xử lý đang tắt hoặc chưa đủ cấu hình");
    expect(render(value)).not.toContain("Không phát hiện ngoại lệ");
  });
});
