// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DesignZoomStage } from "./DesignZoomStage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const rect = {
  x: 100, y: 200, left: 100, top: 200, right: 900, bottom: 800,
  width: 800, height: 600, toJSON: () => ({}),
};
const urls = { originalUrl: "/source.png", thumbnailUrl: "/thumb.png",
  alt: "Thiết kế", positionLabel: "Mặt trước", variantLabel: "Mẫu 1" };

describe("Design gallery pointer-anchored wheel zoom", () => {
  let host: HTMLDivElement | null = null;
  let root: Root | null = null;

  const stage = () => host!.querySelector<HTMLDivElement>(".etsy-design-gallery-zoom-stage")!;

  async function mount(key = "first") {
    if (!host) {
      host = document.createElement("div");
      document.body.append(host);
      root = createRoot(host);
    }
    await act(async () => root?.render(<DesignZoomStage key={key} {...urls} />));
    expect(stage()).not.toBeNull();
    Object.defineProperty(stage(), "getBoundingClientRect", {
      configurable: true, value: () => rect,
    });
  }

  async function wheel(deltaY: number, clientX = 300, clientY = 350) {
    const event = new WheelEvent("wheel", {
      deltaY, clientX, clientY, bubbles: true, cancelable: true,
    });
    await act(async () => { stage().dispatchEvent(event); });
    return event;
  }

  function zoomValues() {
    const current = stage().style;
    return {
      scale: Number(current.getPropertyValue("--etsy-preview-scale")),
      x: Number.parseFloat(current.getPropertyValue("--etsy-preview-x")),
      y: Number.parseFloat(current.getPropertyValue("--etsy-preview-y")),
    };
  }

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    root = null; host?.remove(); host = null;
  });

  it("zooms only the image layers around the pointer and prevents scrolling", async () => {
    await mount();
    expect(zoomValues()).toEqual({ scale: 1, x: 0, y: 0 });
    expect(stage().querySelector(".etsy-design-gallery-zoom-status")).toBeNull();
    const evt = await wheel(-120);
    expect(evt.defaultPrevented).toBe(true);
    const z1 = zoomValues();
    expect(z1.scale).toBeGreaterThan(1);
    expect(z1.scale).toBeLessThan(2);
    expect(z1.x).toBeCloseTo(200 * (1 - z1.scale), 4);
    expect(z1.y).toBeCloseTo(150 * (1 - z1.scale), 4);
    expect(stage().querySelector(".etsy-design-gallery-zoom-status")?.textContent)
      .toContain("Nhấn bánh xe giữa");
    expect(stage().querySelectorAll(".etsy-design-gallery-artwork img")).toHaveLength(2);
    expect(stage().querySelector(".etsy-design-gallery-stage-chip")?.textContent)
      .toContain("Mặt trước");

    await wheel(-120);
    const z2 = zoomValues();
    expect(z2.scale).toBeGreaterThan(z1.scale);
    // The original point stays under the same cursor after each wheel tick.
    expect((200 - z2.x) / z2.scale).toBeCloseTo(200, 4);
    expect((150 - z2.y) / z2.scale).toBeCloseTo(150, 4);

    await wheel(120);
    const z3 = zoomValues();
    expect(z3.scale).toBeCloseTo(z1.scale, 4);
    expect(z3.x).toBeCloseTo(z1.x, 4);
    expect(z3.y).toBeCloseTo(z1.y, 4);
  });

  it("clamps the zoom range, resets by middle mouse click, and never autoscrolls", async () => {
    await mount();
    await wheel(-10000);
    expect(zoomValues().scale).toBeLessThanOrEqual(8);
    for (let i = 0; i < 30; i++) await wheel(-120);
    expect(zoomValues().scale).toBe(8);
    const down = new MouseEvent("mousedown",
      { button: 1, bubbles: true, cancelable: true });
    await act(async () => stage().dispatchEvent(down));
    expect(down.defaultPrevented).toBe(true);
    expect(zoomValues()).toEqual({ scale: 1, x: 0, y: 0 });
    expect(stage().querySelector(".etsy-design-gallery-zoom-status")).toBeNull();
    const aux = new MouseEvent("auxclick",
      { button: 1, bubbles: true, cancelable: true });
    await act(async () => stage().dispatchEvent(aux));
    expect(aux.defaultPrevented).toBe(true);
    await wheel(-120);
    for (let i = 0; i < 40; i++) await wheel(120);
    expect(zoomValues()).toEqual({ scale: 1, x: 0, y: 0 });
  });

  it("resets zoom on switching the design without changing the underlying PNG", async () => {
    await mount();
    await wheel(-120);
    expect(zoomValues().scale).toBeGreaterThan(1);
    await mount("second");
    expect(zoomValues()).toEqual({ scale: 1, x: 0, y: 0 });
    expect(stage().querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-original")
      ?.getAttribute("src")).toBe("/source.png");
    expect(stage().querySelector<HTMLImageElement>(".etsy-design-gallery-artwork-thumb")
      ?.getAttribute("src")).toBe("/thumb.png");
  });
});
