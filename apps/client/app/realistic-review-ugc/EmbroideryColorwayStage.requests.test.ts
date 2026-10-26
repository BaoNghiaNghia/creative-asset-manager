import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { listEcommercePageJobs } from "./EmbroideryColorwayStage";

vi.mock("./api", async importOriginal => ({
  ...await importOriginal<typeof import("./api")>(),
  listEcommerceJobs: vi.fn(),
}));

describe("Stage 2 Ecommerce page batching", () => {
  it("bounds the 500-design job scan to two concurrent API batches", async () => {
    let active = 0, peak = 0;
    const finishers: Array<() => void> = [];
    vi.mocked(api.listEcommerceJobs).mockImplementation(() =>
      new Promise(resolve => {
        active++;
        peak = Math.max(peak, active);
        finishers.push(() => { active--; resolve([]); });
      }));
    const task = listEcommercePageJobs(Array.from({ length: 500 }, (_, index) => "plan-" + index));
    await Promise.resolve();
    expect(peak).toBe(2);
    expect(finishers.length).toBe(2);
    for (let index = 0; index < 10; index++) {
      finishers[index]();
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    await expect(task).resolves.toEqual([]);
    expect(api.listEcommerceJobs).toHaveBeenCalledTimes(10);
    expect(peak).toBe(2);
    expect(active).toBe(0);
  });
});
