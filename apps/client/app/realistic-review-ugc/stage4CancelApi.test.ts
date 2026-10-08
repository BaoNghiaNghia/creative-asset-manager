import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelStage2Jobs } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("Stage 4 cancel contract", () => {
  it("sends only the selected cancellable job IDs, not all queued work", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ cancelled: 1, job_ids: ["new-job"] }),
    });
    vi.stubGlobal("fetch", fetchSpy);
    expect(await cancelStage2Jobs("plan-1", ["new-job"])).toEqual({
      cancelled: 1, job_ids: ["new-job"],
    });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/realistic-review-ugc/source-plans/plan-1/stage2-jobs/cancel");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body)).toEqual({ job_ids: ["new-job"] });
  });
});
