import { describe, expect, it, vi } from "vitest";
import { inventoryDailySheetApi } from "./api";
import { requireVerifiedDisposableWriteback } from "./writebackQa";

describe("Google Sheets disposable QA safety flags", () => {
  it("preflight is credentialed GET only and has no write payload", async () => {
    const mockedFetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ready: false,
        checks: {
          native_source: true, source_revision_available: true,
          source_copy_allowed: false, destination_folder_valid: true,
          destination_folder_can_add: true,
        },
        blockers: ["inventory_qa_source_copy_allowed_blocked"],
      }),
    }));
    vi.stubGlobal("fetch", mockedFetch);
    try {
      const result = await inventoryDailySheetApi.inspectWritebackQaPreflight();
      expect(result.ready).toBe(false);
      expect(result.blockers).toContain("inventory_qa_source_copy_allowed_blocked");
      expect(mockedFetch).toHaveBeenCalledTimes(1);
      expect(mockedFetch).toHaveBeenCalledWith(
        "/api/inventory/daily-sheet/writeback-qa/preflight",
        {credentials:"include", headers:{"Content-Type":"application/json"}},
      );
    } finally { vi.unstubAllGlobals(); }
  });

  it("accepts only independently verified write/readback, disposal and source integrity", () => {
    expect(() => requireVerifiedDisposableWriteback({
      verified: true, disposable_copy_trashed: true, source_file_unchanged_by_qa: true,
    })).not.toThrow();
  });

  it.each([
    [{ verified: false, disposable_copy_trashed: true, source_file_unchanged_by_qa: true }, "verified"],
    [{ verified: true, disposable_copy_trashed: false, source_file_unchanged_by_qa: true }, "disposable_copy_trashed"],
    [{ verified: true, disposable_copy_trashed: true, source_file_unchanged_by_qa: false }, "source_file_unchanged_by_qa"],
    [{ verified: true, disposable_copy_trashed: true }, "source_file_unchanged_by_qa"],
  ])("rejects missing or false proof before showing QA success", (payload, missing) => {
    expect(() => requireVerifiedDisposableWriteback(payload as never))
      .toThrow(new RegExp("Google Sheets QA failed:.*" + missing));
  });
});
