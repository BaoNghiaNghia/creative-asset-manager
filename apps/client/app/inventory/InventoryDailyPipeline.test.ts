import { describe, expect, it, vi } from "vitest";
import { InventoryApiError, type InventoryHistoricalReplayResult } from "./api";
import { isManualRecoveryError, retryFreshHistoricalReplay } from "./InventoryDailyPipeline";

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

describe("Inventory Morning Reset manual recovery routing", () => {
  it("routes only missed-window, preview-ready and stale evidence states to manual recovery", () => {
    expect(isManualRecoveryError("inventory_morning_reset_missed_safe_window")).toBe(true);
    expect(isManualRecoveryError("inventory_morning_reset_manual_recovery_preview_ready")).toBe(true);
    expect(isManualRecoveryError("stale_evidence")).toBe(true);
    expect(isManualRecoveryError("previous_day_gemini_not_verified")).toBe(false);
    expect(isManualRecoveryError(null)).toBe(false);
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
