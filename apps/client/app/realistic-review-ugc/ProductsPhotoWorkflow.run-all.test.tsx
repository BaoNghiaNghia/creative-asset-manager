// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import type { EcommerceRunAllResult } from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    getEcommerceInputReadiness: vi.fn().mockResolvedValue(null),
    getEcommerceRunAllStatus: vi.fn().mockResolvedValue(null),
    runAllEcommerceJobs: vi.fn(),
    runSceneJob: vi.fn(),
    startUgcScout: vi.fn().mockResolvedValue({}),
    getUgcScoutGallery: vi.fn().mockResolvedValue({
      campaign_id: "", items: [], count: 0, status: "ready",
    }),
  };
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const stageNames = ["Ecommerce · Image 01", "Ecommerce · Image 02",
  "Ecommerce · Image 03", "Ecommerce · Image 04",
  "Multi-hat", "UGC", "12 Hat Colors"];
function state(status: "running" | "blocked" | "completed", current_slot: number): EcommerceRunAllResult {
  return {
    run_id: "run-001", source_plan_id: "plan-1", source_revision: "rev-1",
    color_key: "natural-charcoal", status, current_slot,
    error_code: status === "blocked" ? "run_all_slot_failed" : null,
    error_message: status === "blocked" ? "Retry failed UGC output" : null,
    steps: stageNames.map((name, index) => ({
      stage: index < 4 ? "prod_s2_ec" + (index + 1) : index === 4 ? "prod_s2_mh_1"
        : index === 5 ? "prod_s2_ugc_1" : "prod_s2_col",
      slot: index + 1, name,
      completed_outputs: index + 1 < current_slot ? index === 6 ? 12 : index >= 4 ? 3 : 1 : 0,
      total_outputs: index === 6 ? 12 : index >= 4 ? 3 : 1,
      status: index + 1 < current_slot ? "completed"
        : index + 1 > current_slot ? "pending"
        : status === "blocked" ? "blocked" : status === "completed" ? "completed" : "running",
    })),
  };
}
const colorwaySkillSelection = {
  source: "local" as const, skill_id: null,
  skill_name: "s7-8869-scale-12-color", skill_version: null,
};
const props = {
  sourceName: "design.png", sourcePlanId: "plan-1", readiness: null,
  selectedColor: "natural-charcoal",
  colorwaySkillSelection, onEcommerceJobChange: vi.fn(),
};
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  vi.mocked(api.runAllEcommerceJobs).mockReset();
  vi.mocked(api.runSceneJob).mockReset();
  vi.mocked(api.getEcommerceRunAllStatus).mockReset().mockResolvedValue(null);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  document.body.replaceChildren();
});

async function render() {
  await act(async () => root.render(<ProductsPhotoWorkflow {...props} />));
}
function button() {
  return host.querySelector<HTMLButtonElement>('.rrugc-products-photo-footer-action.is-run-all')!;
}

describe("Durable sequential Products Run all", () => {
  it("sends only one server orchestration request, never parallel scene/colorway requests", async () => {
    vi.mocked(api.runAllEcommerceJobs).mockResolvedValue(state("running", 1));
    await render();
    expect(button().disabled).toBe(false);
    await act(async () => button().click());
    expect(api.runAllEcommerceJobs).toHaveBeenCalledTimes(1);
    expect(api.runAllEcommerceJobs).toHaveBeenCalledWith(
      "plan-1", "natural-charcoal", colorwaySkillSelection,
    );
    expect(api.runSceneJob).not.toHaveBeenCalled();
    expect(button().disabled).toBe(true);
    expect(button().textContent).toContain("Slot 1/7");
    expect(host.textContent).toContain("Next slot starts only after this slot completes.");
  });

  it("shows Scout waiting instead of stopped and does not enqueue any other slot", async () => {
    const waiting = state("running", 6);
    waiting.error_code = "run_all_scout_waiting";
    waiting.error_message = "Waiting for three approved, distinct lifestyle Scout images.";
    waiting.steps[5].status = "waiting";
    waiting.steps[5].reason = waiting.error_message;
    vi.mocked(api.getEcommerceRunAllStatus).mockResolvedValue(waiting);
    await render();
    expect(button().disabled).toBe(true);
    expect(button().textContent).toContain("Waiting for Scout");
    expect(host.textContent).toContain("Waiting for Scout · Slot 6/7");
    expect(host.textContent).toContain("Waiting for three approved");
    expect(host.textContent).not.toContain("Stopped at Slot");
    expect(api.runSceneJob).not.toHaveBeenCalled();
  });

  it("restores persisted sequence after reopening page and allows explicit resume only on block", async () => {
    vi.mocked(api.getEcommerceRunAllStatus).mockResolvedValue(state("blocked", 6));
    vi.mocked(api.runAllEcommerceJobs).mockResolvedValue(state("running", 6));
    await render();
    expect(button().textContent).toContain("Resume all");
    expect(host.textContent).toContain("Retry failed UGC output");
    await act(async () => button().click());
    expect(button().disabled).toBe(true);
    expect(button().textContent).toContain("Slot 6/7");
    expect(api.runSceneJob).not.toHaveBeenCalled();
  });
});