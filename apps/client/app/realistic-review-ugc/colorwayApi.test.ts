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

  it("uses the 12-color Products readiness scope but leaves Reviews on 13", async () => {
    const response = { ready: true, error_code: null, stock_ready_count: 12, stock_total_count: 12 };
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => response,
    });
    vi.stubGlobal("fetch", fetchSpy);
    expect(await getColorwayReadiness(undefined, "products")).toEqual(response);
    expect(fetchSpy.mock.calls[0][0]).toBe(
      "/api/v1/realistic-review-ugc/colorways/readiness?workflow=products",
    );
  });

  it("posts only twelve Products colorway jobs when the backend selects 12", async () => {
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 202, json: async () => ({ queued: 12, existing: 0 }),
    });
    vi.stubGlobal("fetch", fetchSpy);
    const result = await queueColorwayBatch(["product-design"], {
      source: "local", skill_id: null, skill_name: "s7-8869-scale-12-color", skill_version: null,
    }, "products");
    expect(result.queued).toBe(12);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toContain("/colorways/batch");
    expect(JSON.parse(init.body).workflow).toBe("products");
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
      source_plan_ids: ["embroidery-a"], workflow: "reviews", skill_source: "local",
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