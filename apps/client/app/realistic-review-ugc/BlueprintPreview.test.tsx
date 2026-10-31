// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import {
  BlueprintPreview, BLUEPRINT_HAT_COLORS, blueprintAvailableColors,
  blueprintHatPhoto, blueprintNavigate, blueprintWindow,
} from "./BlueprintPreview";
import type { GenerationOutputVersion, HatStockCatalog } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const versions: GenerationOutputVersion[] = Array.from({ length: 6 }, (_, index) => ({
  version: 6 - index, output_name: "design_0" + (6 - index) + ".png",
  url: "/api/v1/outputs/" + (6 - index), created_at: "2026-10-09T00:00:00Z",
  width: 1000, height: 1000,
}));
function stock(): HatStockCatalog {
  const colors: HatStockCatalog["colors"] = {};
  for (const color of BLUEPRINT_HAT_COLORS) {
    colors[color.key] = { revision: "revision-1", views: {
      front: "/official/" + color.key + "/front.jpg",
      side: "/official/" + color.key + "/side.jpg",
    } };
  }
  return { can_sync: false, colors };
}
const catalog = stock();
let host: HTMLElement;
let root: ReturnType<typeof createRoot>;
afterEach(() => {
  if (host?.isConnected) host.remove();
});
async function mount(props: Partial<Parameters<typeof BlueprintPreview>[0]> = {}) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<BlueprintPreview versions={versions} stockCatalog={catalog} {...props}/>));
}
async function close() {
  await act(async () => root.unmount());
  document.body.replaceChildren();
}
async function clickButton(label: string) {
  await act(async () => {
    const button = host.querySelector<HTMLButtonElement>('button[aria-label="' + label + '"]');
    expect(button).not.toBeNull();
    button!.click();
  });
}

describe("Blueprint 8869 official stock and embroidery", () => {
  it("uses one verified Front + Side source for every offered color, with no Roberts atlas", () => {
    expect(BLUEPRINT_HAT_COLORS).toHaveLength(12);
    expect(blueprintAvailableColors(catalog)).toHaveLength(12);
    expect(blueprintAvailableColors(null)).toHaveLength(0);
    for (const color of blueprintAvailableColors(catalog)) {
      expect(blueprintHatPhoto(color.id, catalog, "front")).toContain(color.key);
      expect(blueprintHatPhoto(color.id, catalog, "side")).toContain(color.key);
      expect(blueprintHatPhoto(color.id, catalog, "side")).not.toBe(blueprintHatPhoto(color.id, catalog, "front"));
    }
    const incomplete = stock();
    incomplete.colors["natural-brown"].views.side = undefined;
    expect(blueprintAvailableColors(incomplete).some(c => c.id === "brown")).toBe(false);
    expect(blueprintHatPhoto("unknown", catalog)).toBe("");
  });

  it("shows the active Stage 1 source design on matching official front stock immediately", async () => {
    await mount();
    const front = host.querySelector<HTMLElement>(".rrugc-blueprint-selected-hat")!;
    const side = host.querySelector<HTMLElement>(".rrugc-blueprint-side-image.is-stock")!;
    expect(front.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.getAttribute("src"))
      .toBe("/official/white-black/front.jpg");
    expect(side.querySelector<HTMLImageElement>(".rrugc-blueprint-side-hat")?.getAttribute("src"))
      .toBe("/official/white-black/side.jpg");
    const artwork = front.querySelector<HTMLImageElement>(".rrugc-blueprint-selected-design img")!;
    expect(artwork.getAttribute("src")).toContain("artwork=stage1-thread-geometry-v1");
    expect(artwork.getAttribute("src")).toContain("thumbnail=true&size=1024");
    const thumb = host.querySelector<HTMLImageElement>(".rrugc-blueprint-design-choice img")!;
    expect(thumb.getAttribute("src")).toContain("thumbnail=true&size=224&preview_format=webp");
    expect(artwork.style.width).toBe("100%");
    expect(artwork.style.height).toBe("100%");
    expect(artwork.style.opacity).not.toBe("0");
    expect(front.querySelector(".rrugc-blueprint-detail-affordance")?.textContent).toContain("Zoom");
    expect(side.querySelector(".rrugc-blueprint-detail-affordance")?.textContent).toContain("Hover to zoom");
    expect(host.innerHTML).not.toContain("roberts-reference");
    expect(host.innerHTML).not.toContain("sides-atlas");
    expect(host.querySelectorAll(".rrugc-blueprint-side-calibration-design")).toHaveLength(0);
    await close();
  });

  it("switches BOTH angles together when selecting Brown or White/Navy", async () => {
    await mount();
    await clickButton("Select hat color Natural / Brown");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.getAttribute("src"))
      .toBe("/official/natural-brown/front.jpg");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-hat")?.getAttribute("src"))
      .toBe("/official/natural-brown/side.jpg");
    for (let step = 0; step < 9; step++) await clickButton("Next hat color");
    await clickButton("Select hat color White / Navy");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.getAttribute("src"))
      .toBe("/official/white-navy/front.jpg");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-hat")?.getAttribute("src"))
      .toBe("/official/white-navy/side.jpg");
    await clickButton("Next design columns");
    expect(host.querySelector(".rrugc-blueprint-selection-a11y")?.textContent).toContain("design_05.png");
    await close();
  });

  it("keeps a real hover lens for BOTH synchronized stock photos, even while art is still rendering", async () => {
    await mount();
    const setup = (element: HTMLElement) => {
      Object.defineProperty(element, "clientWidth", { configurable: true, value: 400 });
      Object.defineProperty(element, "clientHeight", { configurable: true, value: 400 });
      element.getBoundingClientRect = () => ({
        left: 0, top: 0, right: 400, bottom: 400, width: 400, height: 400,
      } as DOMRect);
    };
    const hover = (node: HTMLElement) => {
      const event = new Event("pointermove", { bubbles: true });
      Object.defineProperties(event, {
        clientX: { value: 200 }, clientY: { value: 200 },
        pointerType: { value: "mouse" }, buttons: { value: 0 },
      });
      node.dispatchEvent(event);
    };
    const front = host.querySelector<HTMLElement>(".rrugc-blueprint-selected-hat")!;
    const side = host.querySelector<HTMLElement>(".rrugc-blueprint-side-image.is-stock")!;
    for (const pane of [front, side]) {
      setup(pane);
      pane.querySelector<HTMLElement>(".rrugc-blueprint-smooth-img")!.classList.add("is-loaded");
      await act(async () => hover(pane));
      const lens = pane.querySelector<HTMLElement>(".rrugc-blueprint-magnifier")!;
      expect(lens.style.opacity).toBe("1");
      expect(lens.style.width).toBe("164px");
    }
    expect(side.querySelector<HTMLImageElement>(".rrugc-blueprint-magnifier-scene img")?.getAttribute("src"))
      .toBe("/official/white-black/side.jpg");
    await act(async () => side.dispatchEvent(new Event("pointerout", { bubbles: true })));
    expect(side.querySelector<HTMLElement>(".rrugc-blueprint-magnifier")?.style.opacity).toBe("0");
    await close();
  });

  it("offers a detail modal and functional front size adjustment without fake side lettering", async () => {
    await mount({ guides: true });
    expect(host.querySelectorAll(".rrugc-blueprint-view-zoom")).toHaveLength(1);
    expect(host.querySelector('input[aria-label="Front design zoom"]')).not.toBeNull();
    expect(host.querySelector(".rrugc-blueprint-view-zoom-caption")).toBeNull();
    expect(host.querySelector(".rrugc-blueprint-view-zoom output")?.textContent).toBe("100%");
    expect(host.querySelector(".rrugc-blueprint-view-zoom")?.textContent).not.toContain("Design");
    expect(host.querySelector('input[aria-label="Right side design zoom"]')).toBeNull();
    expect(host.querySelectorAll(".rrugc-blueprint-front-guide")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-blueprint-side-guides")).toHaveLength(0);
    expect(host.querySelector(".rrugc-blueprint-side-guide-note")?.textContent)
      .toContain("prior atlas hoop calibration not applied");
    await act(async () => host.querySelector<HTMLElement>(".rrugc-blueprint-front-open-detail")!.click());
    expect(document.querySelector(".rrugc-blueprint-detail-dialog")).not.toBeNull();
    const detailArt = document.querySelector<HTMLImageElement>(".rrugc-blueprint-detail-image .rrugc-blueprint-selected-design img")!;
    expect(detailArt.getAttribute("src")).toContain("artwork=stage1-thread-geometry-v1");
    // Until the on-demand high-res composite resolves, the detail viewer
    // shows the existing fast preview rather than a blank canvas.
    expect(detailArt.getAttribute("src")).toContain("thumbnail=true");
    const closeButton = document.querySelector<HTMLButtonElement>('button[aria-label="Close front embroidery detail"]')!;
    await act(async () => closeButton.click());
    expect(document.querySelector(".rrugc-blueprint-detail-dialog")).toBeNull();
    await close();
  });

  it("reports missing official side stock instead of displaying another hat style", async () => {
    await mount();
    const img = host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-image.is-stock .rrugc-blueprint-side-hat")!;
    await act(async () => img.dispatchEvent(new Event("error", { bubbles: false })));
    expect(host.querySelector(".rrugc-blueprint-side-unavailable")?.textContent)
      .toContain("Official 8869 side stock unavailable");
    expect(host.innerHTML).not.toContain("Roberts");
    await close();
  });

  it("preserves selection actions, slide helpers and empty-gallery behavior", async () => {
    const onStar = (_version: GenerationOutputVersion) => {};
    await mount({ onToggleStage2Selection: onStar });
    expect(host.querySelectorAll(".rrugc-blueprint-design-choice")).toHaveLength(versions.length);
    expect(host.querySelectorAll(".rrugc-blueprint-design-star")).toHaveLength(versions.length);
    expect(host.querySelectorAll(".rrugc-blueprint-design-download")).toHaveLength(versions.length);
    expect(blueprintWindow(versions, 99).items).toHaveLength(5);
    expect(blueprintNavigate(0, 0, "left", 6)).toEqual({ design: 0, color: 0 });
    expect(blueprintNavigate(5, 11, "down", 6)).toEqual({ design: 5, color: 11 });
    expect(renderToStaticMarkup(<BlueprintPreview versions={[]} stockCatalog={catalog} />))
      .toContain("No designs available");
    await close();
  });
});