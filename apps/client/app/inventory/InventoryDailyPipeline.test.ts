import { describe, expect, it, vi } from "vitest";
import { InventoryApiError, inventoryLifecycleApi, type InventoryHistoricalReplayResult } from "./api";
import { inventoryDailyHealth, isManualRecoveryError, isMaterialRecoveryError, morningResetRecoveryAction, previousInventoryBusinessDate, retryFreshHistoricalReplay, sourceVerifiedForRecovery, parseInventoryDiagnosticDates, isCurrentInventoryBusinessDate, inventoryMorningRecoveryGateMessage } from "./InventoryDailyPipeline";

const replayResult: InventoryHistoricalReplayResult = {
  run_id: "replay-3",
  business_date: "2030-08-09",
  mode: "fresh_copy",
  status: "completed",
  verification_status: "verified",
  promoted: true,
  source_snapshot_file_id: "snapshot",
  previous_gemini_file_id: "old-gemini",
  replay_gemini_file_id: "new-gemini",
  model: "gemini-test",
  plan_hash: "plan",
  writes: 3,
};

describe("Inventory date-scoped recovery guard", () => {
  it("does not offer historic Reset recovery on the shared current-day workbook", () => {
    const moment=new Date("2026-10-16T07:00:00.000Z");
    expect(isCurrentInventoryBusinessDate("2026-10-15",moment)).toBe(false);
    expect(isCurrentInventoryBusinessDate("2026-10-16",moment)).toBe(true);
    expect(isCurrentInventoryBusinessDate("2026-10-17",moment)).toBe(false);
    expect(inventoryMorningRecoveryGateMessage("inventory_morning_reset_recovery_current_day_only"))
      .toContain("không được Apply Reset");
    expect(inventoryMorningRecoveryGateMessage("preview_candidate"))
      .toContain("Backend sẽ kiểm tra lại");
  });
});

describe("Inventory incident evidence viewer", () => {
  it("accepts only 1–8 distinct real ISO dates and preserves requested order", () => {
    expect(parseInventoryDiagnosticDates("2026-10-08, 2026-10-13,2026-10-15")).toEqual([
      "2026-10-08", "2026-10-13", "2026-10-15",
    ]);
    for (const text of [
      "", "2026-10-08,", "2026-02-30", "2026-10-08,2026-10-08",
      "2026-10-08,random", "08/10/2026",
      Array.from({length: 9}, (_,i)=>`2026-10-${String(i+1).padStart(2,"0")}`).join(","),
    ]) expect(() => parseInventoryDiagnosticDates(text)).toThrow();
  });
  it("uses the tenant-authenticated read-only endpoint without posting or copying Sheets", async () => {
    const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({items:[{business_date:"2026-10-08",profiles:[]}]})});
    vi.stubGlobal("fetch",fetcher);
    try{
      const result=await inventoryLifecycleApi.inspectHistoricalDates(["2026-10-08","2026-10-13"]);
      expect(result.items[0].business_date).toBe("2026-10-08");
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(fetcher.mock.calls[0][0]).toBe(
        "/api/inventory/daily-sheet/gemini/replay/diagnostics?dates=2026-10-08%2C2026-10-13"
      );
      expect(fetcher.mock.calls[0][1].method).toBeUndefined();
      expect(fetcher.mock.calls[0][1].credentials).toBe("include");
    } finally { vi.unstubAllGlobals(); }
  });
});

describe("Inventory Morning Reset manual recovery routing", () => {
  it("routes recoverable post-window carry-forward failures to manual recovery", () => {
    for (const code of [
      "inventory_morning_reset_missed_safe_window",
      "inventory_morning_reset_manual_recovery_preview_ready",
      "stale_evidence",
      "carry_forward_plan_has_issues",
      "empty_carry_forward_plan",
      "closing_opening_mismatch",
      "unknown_material",
      "unknown_warehouse",
      "inventory_gemini_rate_limited",
      "inventory_gemini_transport_error",
    ]) expect(isManualRecoveryError(code)).toBe(true);
    expect(isManualRecoveryError("previous_day_gemini_not_verified")).toBe(false);
    expect(isManualRecoveryError("inventory_gemini_auth_or_permission_error")).toBe(false);
    expect(isManualRecoveryError(null)).toBe(false);
  });

  it("routes material mapping failures to the material registry", () => {
    expect(isMaterialRecoveryError("carry_forward_plan_has_issues")).toBe(true);
    expect(isMaterialRecoveryError("unknown_material")).toBe(true);
    expect(isMaterialRecoveryError("unknown_warehouse")).toBe(false);
    expect(isMaterialRecoveryError(null)).toBe(false);
  });
});

describe("Inventory lifecycle monitoring and paginated recovery safety", () => {
  const createItem = (day: string, overall: "failed" | "completed" | "review_required", verified: boolean, errorCode: string | null = null) => ({
    business_date: day, overall_status: overall, current_stage: "morning_reset",
    previous_day_verified: false, updated_at: null,
    files: { shared_url: null, snapshot_url: "https://example.test/snapshot", gemini_url: null },
    invariants: [],
    action_required: errorCode ? { code: errorCode, stage: "morning_reset", label: "Xử lý" } : null,
    stages: [
      { key: "morning_reset", label: "Reset", status: overall === "failed" ? "failed" : "completed", error_code: errorCode },
      { key: "verified", label: "Xác minh", status: verified ? "completed" : "blocked" },
    ],
  } as import("./api").InventoryLifecycleHistoryItem);

  it("uses authoritative previous-day verification when the prior day is outside the page", () => {
    const current = { ...createItem("2026-10-15", "failed", false, "previous_day_gemini_not_verified"), previous_day_verified: true };
    expect(sourceVerifiedForRecovery(current, [current])).toBe(true);
    expect(sourceVerifiedForRecovery({ ...current, previous_day_verified: false }, [current])).toBe(false);
  });

  it("shows blocked and unverified days separately from successfully verified days", () => {
    const rows = [
      createItem("2026-10-15", "failed", false, "previous_day_gemini_not_verified"),
      createItem("2026-10-14", "review_required", false, "inventory_historical_replay_review_required"),
      createItem("2026-10-13", "completed", true),
    ];
    expect(inventoryDailyHealth(rows)).toEqual({
      total: 3, verified: 1, attention: 2, awaitingVerification: 2, retrying: 0,
    });
  });
});

describe("Inventory dependency recovery routing", () => {
  it("routes missing prior-day Gemini to queued replay, then to preview after verification", () => {
    expect(previousInventoryBusinessDate("2026-10-15")).toBe("2026-10-14");
    expect(previousInventoryBusinessDate("2026-01-01")).toBe("2025-12-31");
    expect(morningResetRecoveryAction("previous_day_gemini_not_verified", false)).toBe("replay_previous");
    expect(morningResetRecoveryAction("previous_day_gemini_not_verified", true)).toBe("preview");
    expect(morningResetRecoveryAction("inventory_morning_reset_missed_safe_window", false)).toBe("preview");
    expect(morningResetRecoveryAction(null, false)).toBe("rerun");
  });
});

describe("Inventory historical replay retry", () => {
  it("retries retryable fresh-copy failures with bounded backoff and succeeds", async () => {
    const run = vi.fn()
      .mockRejectedValueOnce(new InventoryApiError(409, "timeout", {
        code: "inventory_gemini_transport_error",
        category: "TRANSPORT",
        retryable: true,
      }))
      .mockRejectedValueOnce(new InventoryApiError(409, "rate limited", {
        code: "inventory_gemini_rate_limited",
        category: "RATE_LIMIT",
        retryable: true,
      }))
      .mockResolvedValueOnce(replayResult);
    const sleep = vi.fn(async () => undefined);
    const onRetry = vi.fn();

    await expect(retryFreshHistoricalReplay(run, onRetry, sleep)).resolves.toEqual(replayResult);
    expect(run).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenNthCalledWith(1, 1500);
    expect(sleep).toHaveBeenNthCalledWith(2, 4000);
    expect(onRetry).toHaveBeenCalledTimes(2);
  });

  it("does not retry non-retryable auth or permission failures", async () => {
    const error = new InventoryApiError(409, "permission denied", {
      code: "inventory_gemini_auth_or_permission_error",
      category: "AUTH",
      retryable: false,
    });
    const run = vi.fn().mockRejectedValue(error);
    const sleep = vi.fn(async () => undefined);

    await expect(retryFreshHistoricalReplay(run, undefined, sleep)).rejects.toBe(error);
    expect(run).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("stops after three retryable failures", async () => {
    const error = new InventoryApiError(409, "provider unavailable", {
      code: "inventory_gemini_request_failed_503",
      category: "TRANSPORT",
      retryable: true,
    });
    const run = vi.fn().mockRejectedValue(error);
    const sleep = vi.fn(async () => undefined);

    await expect(retryFreshHistoricalReplay(run, undefined, sleep)).rejects.toBe(error);
    expect(run).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
  });
});
