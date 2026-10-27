import { describe, expect, it, vi } from "vitest";
import { loadAllProductStage2SourcePlans, PRODUCT_STAGE2_SOURCE_BATCH_SIZE } from "./ProductsStage2SourcePlans";
import type { SourcePlanPage, SourcePlan } from "./types";

function page(total: number, number: number, size: number): SourcePlanPage {
  const first = (number - 1) * size;
  return {
    items: Array.from({ length: Math.max(0, Math.min(size, total - first)) },
      (_, i) => ({ id: "plan-" + (first + i), source_keyword: "Design " + (first + i) } as SourcePlan)),
    page: number, page_size: size, total,
    overview: {
      embroidery_groups: total, source_images: total, working_groups: 0, refs_loaded: 0,
      stage2_groups: total, stage2_source_images: total, stage2_drive_ready_refs: 0,
      stage2_active_jobs: 0,
    },
  };
}

describe("Products Stage 2 loads every design into one source tree", () => {
  it("loads all 46 designs in one server-sized batch, not just 20", async () => {
    const load = vi.fn(async (options: { page: number; pageSize: number }) =>
      page(46, options.page, options.pageSize));
    const result = await loadAllProductStage2SourcePlans(load, "", "source", "asc");
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith({
      page: 1, pageSize: PRODUCT_STAGE2_SOURCE_BATCH_SIZE, query: "", sortBy: "source",
      sortDirection: "asc", colorwaySources: true, colorwayWorkflow: "products",
    }, undefined);
    expect(result.items).toHaveLength(46);
    expect(result.total).toBe(46);
    expect(result.page).toBe(1);
    expect(result.page_size).toBe(46);
  });

  it("automatically continues across multiple pages when there are more than 500 designs", async () => {
    const load = vi.fn(async (options: { page: number; pageSize: number }) =>
      page(546, options.page, options.pageSize));
    const result = await loadAllProductStage2SourcePlans(load, "jesus", "source", "desc");
    expect(load).toHaveBeenCalledTimes(2);
    expect(load.mock.calls.map(([opts]) => opts.page)).toEqual([1, 2]);
    expect(result.items).toHaveLength(546);
    expect(result.items[545].id).toBe("plan-545");
    expect(result.page_size).toBe(546);
  });

  it("does not partially publish a result after cancellation", async () => {
    const controller = new AbortController();
    const load = vi.fn(async (options: { page: number; pageSize: number }) => {
      if (options.page === 1) controller.abort();
      return page(546, options.page, options.pageSize);
    });
    await expect(loadAllProductStage2SourcePlans(
      load, "", "source", "asc", controller.signal,
    )).rejects.toMatchObject({ name: "AbortError" });
    expect(load).toHaveBeenCalledTimes(1);
  });
});
