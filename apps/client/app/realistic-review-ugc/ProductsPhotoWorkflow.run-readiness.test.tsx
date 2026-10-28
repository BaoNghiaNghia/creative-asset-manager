// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import type { EcommerceInputReadiness, EcommerceJob } from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    getEcommerceInputReadiness: vi.fn(),
    startUgcScout: vi.fn().mockResolvedValue({}),
    getUgcScoutGallery: vi.fn().mockResolvedValue({ campaign_id: "", items: [], count: 0, status: "ready" }),
    runEcommerceJob: vi.fn().mockResolvedValue({ job_id: "new-job", status: "queued" }),
  };
});

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const run = vi.mocked(api.runEcommerceJob);
const preflight = vi.mocked(api.getEcommerceInputReadiness);
const stages = ["prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4"] as const;
const names = {
  prod_s2_ec1: "8869-ecom-front - Fine Thread v1",
  prod_s2_ec2: "8869-ecom-threequatrer - Fine Thread v1",
  prod_s2_ec3: "8869-ecom-side - Fine Thread v1",
  prod_s2_ec4: "8869-ecom-back - Fine Thread v1",
};

function ready(color_key = "natural-charcoal", front = false): EcommerceInputReadiness {
  return {
    source_plan_id: "plan-1", source_revision: "revision-1", color_key,
    slots: stages.map(stage => ({
      stage, view: stage,
      required_roles: stage === "prod_s2_ec4" ? ["stock_back"] : stage === "prod_s2_ec2"
        ? ["design", "embroidered_front", "stock_three_quarter"] : ["design", "stock_front"],
      missing_roles: !front && (stage === "prod_s2_ec2" || stage === "prod_s2_ec3")
        ? ["embroidered_front"] : [],
      ready: front || stage === "prod_s2_ec1" || stage === "prod_s2_ec4",
      status: front || stage === "prod_s2_ec1" || stage === "prod_s2_ec4" ? "ready" : "missing_inputs",
    })),
  };
}

const props = {
  sourceName: "test design", sourcePlanId: "plan-1", selectedColor: "natural-charcoal",
  designSrc: "/design.png", stockCatalog: {
    "natural-charcoal": { views: {front:"/stock-front.jpg", side:"/stock-side.jpg", back:"/stock-back.jpg", "three-quarter":"/stock-three-quarter.jpg"} },
    "white-red": { views: {front:"/white-red-front.jpg", side:"/white-red-side.jpg", back:"/white-red-back.jpg", "three-quarter":"/white-red-three-quarter.jpg"} },
  },
  configuredSkillNames: names, readiness: null,
  onEcommerceJobChange: vi.fn(),
};
let host: HTMLElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  run.mockClear();
  preflight.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  document.body.replaceChildren();
});

function runButton(slot: number) {
  return host.querySelector<HTMLButtonElement>(
    '[data-skill="prod_s2_ec' + slot + '"] .rrugc-products-photo-run-button',
  )!;
}
function runState(slot: number) {
  return host.querySelector<HTMLElement>(
    '[data-skill="prod_s2_ec' + slot + '"] .rrugc-products-photo-run-state',
  );
}
async function render(extra: Partial<typeof props> & { ecommerceJobs?: EcommerceJob[] } = {}) {
  await act(async () => root.render(<ProductsPhotoWorkflow {...props} {...extra} />));
}

describe("Ecommerce Run unlock after Fine Thread upgrade", () => {
  it("loads server readiness immediately; front and back run without opening Details", async () => {
    preflight.mockResolvedValue(ready());
    await render();
    expect(preflight).toHaveBeenCalledWith("plan-1", expect.anything(), "natural-charcoal");
    expect(runButton(1).className).toContain("is-ready");
    expect(runButton(4).className).toContain("is-ready");
    expect(runButton(1).disabled).toBe(false);
    expect(runButton(1).getAttribute("aria-disabled")).toBeNull();
    expect(runState(2)?.className).toContain("is-blocked");
    expect(runState(3)?.className).toContain("is-blocked");
    await act(async () => runButton(1).click());
    expect(run).toHaveBeenCalledWith("plan-1", "prod_s2_ec1", "natural-charcoal");
    expect(preflight).toHaveBeenCalledTimes(2);
  });

  it("unlocks dependent slots after Front completes, without expanding them", async () => {
    preflight.mockImplementation(async () => ready("natural-charcoal", preflight.mock.calls.length > 1));
    await render();
    expect(runState(2)?.className).toContain("is-blocked");
    const frontJob: EcommerceJob = {
      id: "old-front", source_plan_id: "plan-1", stage: "prod_s2_ec1", color_key: "natural-charcoal",
      status: "completed", retry_count: 0, attempt_count: 1,
      error_code: null, output_url: "/front.png", skill_name: names.prod_s2_ec1,
    };
    await render({ ecommerceJobs: [frontJob] });
    expect(preflight).toHaveBeenCalledTimes(2);
    expect(runButton(2).className).toContain("is-ready");
    expect(runButton(3).className).toContain("is-ready");
    expect(runState(1)?.className).toContain("is-completed");
    expect(runButton(1)).toBeNull();
    await act(async () => runButton(2).click());
    expect(run).toHaveBeenCalledWith("plan-1", "prod_s2_ec2", "natural-charcoal");
  });

  it("displays the actual three-quarter stock as Skill 2 edit target, not synthetic front/side", async () => {
    preflight.mockResolvedValue(ready("natural-charcoal", true));
    await render();
    await act(async () => host.querySelector<HTMLButtonElement>(
      '[data-skill="prod_s2_ec2"] .rrugc-products-photo-detail-toggle')!.click());
    const detail = host.querySelector('[data-skill="prod_s2_ec2"] .rrugc-products-photo-inputs')!;
    expect(detail.textContent).toContain("Official 3/4 stock");
    expect(detail.textContent).toContain("edit target");
    expect(detail.querySelector('img[src="/stock-three-quarter.jpg"]')).not.toBeNull();
    expect(detail.textContent).not.toContain("Official right-side geometry");
  });

  it("never enables a different hat color from a stale preflight", async () => {
    let resolveNew: ((data: EcommerceInputReadiness) => void) | null = null;
    preflight.mockResolvedValueOnce(ready()).mockImplementationOnce(() =>
      new Promise<EcommerceInputReadiness>(resolve => { resolveNew = resolve; }),
    );
    await render();
    expect(runButton(1).className).toContain("is-ready");
    await render({selectedColor:"white-red"});
    expect(runState(1)?.className).toContain("is-checking");
    expect(runButton(1)).toBeNull();
    await act(async () => { resolveNew?.(ready("white-red")); });
    expect(runButton(1).className).toContain("is-ready");
  });
});