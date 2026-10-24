import { afterEach, describe, expect, it, vi } from "vitest";
import { RrugcApiError, runSceneJob } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("Stage 2 Skill run errors", () => {
  it("shows the server's structured error and code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: "ecommerce_stock_unavailable", message: "Verified stock missing" },
    }), { status: 503, headers: { "Content-Type": "application/json" } })));
    await expect(runSceneJob("plan-1", "prod_s2_ugc")).rejects.toMatchObject({
      status: 503, code: "ecommerce_stock_unavailable", message: "Verified stock missing",
    } satisfies Partial<RrugcApiError>);
  });

  it("shows validation details instead of [object Object]", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: [{ loc: ["body", "source_plan_id"], msg: "Field required", type: "missing" }],
    }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(runSceneJob("plan-1", "prod_s2_mh")).rejects.toThrow("Invalid request: Field required");
  });

  it("includes HTTP status when proxy replies with HTML", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>Gateway unavailable</html>", {
      status: 502, headers: { "Content-Type": "text/html" },
    })));
    await expect(runSceneJob("plan-1", "prod_s2_mh")).rejects.toThrow("Request failed (HTTP 502).");
  });
});
