import { describe, expect, it } from "vitest";
import { requireVerifiedDisposableWriteback } from "./writebackQa";

describe("Google Sheets disposable QA safety flags", () => {
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
