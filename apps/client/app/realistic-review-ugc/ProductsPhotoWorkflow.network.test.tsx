// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";

vi.mock("./api", async importOriginal => ({
  ...await importOriginal<typeof import("./api")>(),
  getEcommerceInputReadiness: vi.fn().mockResolvedValue({ source_plan_id: "one", color_key: "natural-black", slots: [] }),
  getEcommerceRunAllStatus: vi.fn().mockResolvedValue(null),
  startUgcScout: vi.fn().mockResolvedValue({ status: "ready" }),
  getUgcScoutGallery: vi.fn().mockResolvedValue({ campaign_id: null, status: "ready", items: [], count: 0 }),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class MockObserver {
  static observed: Array<{ target: Element; callback: IntersectionObserverCallback }> = [];
  readonly callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) { this.callback = callback; }
  observe(target: Element) { MockObserver.observed.push({ target, callback: this.callback }); }
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  MockObserver.observed = [];
  vi.stubGlobal("IntersectionObserver", MockObserver);
  vi.mocked(api.getEcommerceInputReadiness).mockClear();
  vi.mocked(api.getEcommerceRunAllStatus).mockClear();
  vi.mocked(api.startUgcScout).mockClear();
  vi.mocked(api.getUgcScoutGallery).mockClear();
});
afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });

describe("Stage 2 lazy row requests", () => {
  it("does not run automatic per-design probes until each design enters the viewport", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<div>{Array.from({ length: 20 }, (_, index) =>
      <ProductsPhotoWorkflow key={index} sourcePlanId={"plan-" + index} sourceName={"Design " + index}
        readiness={null} />
    )}</div>));
    expect(host.querySelectorAll(".rrugc-products-photo-pipeline")).toHaveLength(20);
    expect(vi.mocked(api.getEcommerceInputReadiness)).not.toHaveBeenCalled();
    expect(vi.mocked(api.getEcommerceRunAllStatus)).not.toHaveBeenCalled();
    expect(vi.mocked(api.startUgcScout)).not.toHaveBeenCalled();

    const first = MockObserver.observed.find(({ target }) =>
      target.getAttribute("aria-label") === "Seven-skill photo workflow for Design 0");
    const second = MockObserver.observed.find(({ target }) =>
      target.getAttribute("aria-label") === "Seven-skill photo workflow for Design 1");
    expect(first).toBeDefined();
    expect(second).toBeDefined();
    await act(async () => {
      for (const observer of [first!, second!]) {
        observer.callback([{ target: observer.target, isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
      }
      await Promise.resolve();
    });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(vi.mocked(api.getEcommerceInputReadiness)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.getEcommerceRunAllStatus)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.startUgcScout)).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.getEcommerceInputReadiness).mock.calls.map(args => args[0]).sort()).toEqual(["plan-0", "plan-1"]);
    await act(async () => root.unmount());
  });
});
