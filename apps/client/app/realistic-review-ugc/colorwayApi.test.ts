import { afterEach, describe, expect, it, vi } from "vitest";
import { listColorwayJobs, queueColorwayBatch, retryColorway, getColorwayReadiness } from "./api";

afterEach(() => { vi.unstubAllGlobals(); });

describe("Stage 2 colorway request contract", () => {
  it("reads server readiness without creating processing jobs", async () => {
    const preflight = { ready: false, error_code: "colorway_stock_missing", stock_ready_count: 12, stock_total_count: 13 };
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => preflight });
    vi.stubGlobal("fetch", fetchSpy);
    expect(await getColorwayReadiness()).toEqual(preflight);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/realistic-review-ugc/colorways/readiness");
    expect(init.credentials).toBe("include");
    expect(init.method).toBeUndefined();
  });

  it("posts the backend skill_source field, not a bare source field", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ queued: 13, existing: 0 }),
    });
    vi.stubGlobal("fetch", fetchSpy);
    const result = await queueColorwayBatch(["embroidery-a"], {
      source: "local", skill_id: null, skill_name: "gatorhats-8869-image-studio", skill_version: "1",
    });
    expect(result).toEqual({ queued: 13, existing: 0 });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/realistic-review-ugc/colorways/batch");
    const payload = JSON.parse(init.body);
    expect(payload).toEqual({
      source_plan_ids: ["embroidery-a"], skill_source: "local",
      skill_id: null, skill_name: "gatorhats-8869-image-studio", skill_version: "1",
    });
    expect(payload).not.toHaveProperty("source");
    expect(init.credentials).toBe("include");
  });

  it("uses tenant-authenticated GET requests for the selected source IDs", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ([]),
    });
    vi.stubGlobal("fetch", fetchSpy);
    const result = await listColorwayJobs(["source-a", "source-a", "source-b"]);
    expect(result).toEqual([]);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toContain("source_plan_id=source-a");
    expect(url).toContain("source_plan_id=source-b");
    expect((url.match(/source_plan_id=/g) || []).length).toBe(2);
    expect(init.credentials).toBe("include");
  });

  it("retries only the specified colorway ID via the retry endpoint", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 202, json: async () => ({ job_id: "job-1", status: "queued" }),
    });
    vi.stubGlobal("fetch", fetchSpy);
    await retryColorway("job-1");
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/v1/realistic-review-ugc/colorways/job-1/retry");
    expect(init.method).toBe("POST");
  });
});
