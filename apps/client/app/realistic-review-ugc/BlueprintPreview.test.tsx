// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { BlueprintPreview, BLUEPRINT_HAT_COLORS, BLUEPRINT_SIDE_ATLAS, BLUEPRINT_SIDE_CALIBRATION_DESIGN, blueprintNavigate, blueprintWindow, blueprintSidePosition, blueprintHatPhoto } from "./BlueprintPreview";
import type { GenerationOutputVersion } from "./api";
import { RIGHT_SIDE_GUIDE } from "./BlueprintSideGuides";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const versions: GenerationOutputVersion[] = Array.from({ length: 9 }, (_, i) => ({
  version: 9 - i,
  output_name: "design_" + (9 - i) + ".png",
  url: "/api/v1/outputs/" + (9 - i),
  created_at: "2026-10-09T00:00:00Z",
  width: 1000, height: 1000,
}));

afterEach(() => document.body.replaceChildren());

function chosen(host: HTMLElement) {
  return host.querySelector<HTMLElement>(".rrugc-blueprint-selected-info")!.textContent!;
}

function click(host: HTMLElement, label: string) {
  const button = host.querySelector<HTMLButtonElement>('button[aria-label="' + label + '"]')!;
  expect(button).not.toBeNull();
  button.click();
}

function sendKey(key: string) {
  return act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  });
}

function drag(track: Element, x: number, y: number, endX: number, endY: number) {
  return act(async () => {
    for (const [type, left, top] of [
      ["pointerdown", x, y], ["pointermove", endX, endY], ["pointerup", endX, endY],
    ] as const) {
      const event = new Event(type, { bubbles: true });
      Object.defineProperties(event, {
        button: { value: 0 }, buttons: { value: type === "pointerup" ? 0 : 1 },
        pointerType: { value: "mouse" }, pointerId: { value: 5 },
        clientX: { value: left }, clientY: { value: top },
      });
      track.dispatchEvent(event);
    }
  });
}

describe("Stage 1 Blueprint Cross Puzzle", () => {
  it("uses updated 12-color front photography and preserves legacy Khaki/Maroon", () => {
    for (const color of BLUEPRINT_HAT_COLORS) {
      const url = blueprintHatPhoto(color.id);
      if (color.id === "khaki-maroon") expect(url).toBe("/rrugc/blueprint/fronts/khaki-maroon.jpg");
      else expect(url).toBe("/rrugc/blueprint/fronts/natural-" + color.id + ".webp");
    }
  });

  it("renders ONE horizontal design carousel, ONE vertical color carousel, ONE fixed preview, not a table", () => {
    expect(BLUEPRINT_HAT_COLORS).toHaveLength(13);
    const html = renderToStaticMarkup(<BlueprintPreview versions={versions} />);
    expect(html).not.toContain("9 designs × 13 hat colors");
    expect(html).not.toContain("rrugc-blueprint-description");
    expect(html).not.toContain("rrugc-blueprint-controls");
    expect(html).toContain("Low-relief material composite · Thread color by hat");
    expect(html).not.toContain("rrugc-blueprint-matrix");
    expect(html).not.toContain("rrugc-blueprint-cell");
    expect(html).toContain("rrugc-blueprint-design-carousel");
    expect(html).not.toContain("rrugc-blueprint-cross-corner");
    expect(html).not.toContain("13 colorways");
    expect(html).not.toContain("translateX(calc(-50%");
    expect((html.match(/rrugc-blueprint-design-item/g) || []).length).toBe(versions.length);
    expect(html).toContain("rrugc-blueprint-color-carousel");
    expect(html).toContain("Front and right-facing side previews for selected hat");
    expect((html.match(/class="rrugc-blueprint-panel-header"/g) || []).length).toBe(2);
    expect(html).not.toContain("rrugc-blueprint-selected-top");
    expect(html).not.toContain("rrugc-blueprint-selected-badge");
    expect(html).toContain('aria-label="Front design zoom"');
    expect(html).toContain('Auto-match thread');
    expect(html).not.toContain("rrugc-blueprint-panel-footer");
    expect(html).not.toContain("View original design");
    expect(html).not.toContain("Roberts sizing reference · Right-side placement approximate");
    expect(html).toContain("rrugc-blueprint-selection-a11y");
    expect((html.match(/class="rrugc-blueprint-selected-hat rrugc-blueprint-front-open-detail"/g) || []).length).toBe(1);
    expect((html.match(/class="rrugc-blueprint-front-scene"/g) || []).length).toBe(2);
    expect((html.match(/rrugc-blueprint-design-choice/g) || []).length).toBe(versions.length);
    expect((html.match(/rrugc-blueprint-color-choice/g) || []).length).toBeLessThanOrEqual(7);
    expect(html).toContain("design_9.png");
    expect(html).toContain("Natural / Black");
  });

  it("opens a full-quality front embroidery detail modal, zooms, and closes without losing selection", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const front = host.querySelector<HTMLElement>(".rrugc-blueprint-front-open-detail")!;
    expect(front.getAttribute("role")).toBe("button");
    expect(front.getAttribute("tabindex")).toBe("0");
    expect(front.getAttribute("aria-label")).toContain("Enlarge front embroidery");
    await act(async () => front.click());
    expect(document.querySelectorAll(".rrugc-blueprint-detail-dialog")).toHaveLength(1);
    expect(document.querySelector(".rrugc-blueprint-detail-dialog")?.getAttribute("aria-modal")).toBe("true");
    const image = document.querySelector<HTMLImageElement>(".rrugc-blueprint-detail-image .rrugc-blueprint-selected-design img")!;
    // Detail fetches the full output, never the 512px preview thumbnail.
    expect(image.getAttribute("src")).toBe(versions[0].url + "?artwork=stage1-thread-geometry-v1");
    expect(document.querySelector<HTMLImageElement>(".rrugc-blueprint-detail-image .rrugc-blueprint-hat-original")?.src).toContain("/rrugc/blueprint/fronts/natural-black.webp");
    await act(async () => click(document.body, "Zoom in front detail"));
    expect(document.querySelector(".rrugc-blueprint-detail-zoom-controls output")?.textContent).toBe("125%");
    expect(document.querySelector<HTMLElement>(".rrugc-blueprint-detail-pan")?.style.transform).toContain("scale(1.25)");
    await act(async () => click(document.body, "Close front embroidery detail"));
    expect(document.querySelector(".rrugc-blueprint-detail-dialog")).toBeNull();
    expect(chosen(host)).toContain("design_9.png");
    await act(async () => { front.focus(); front.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    expect(document.querySelector(".rrugc-blueprint-detail-dialog")).not.toBeNull();
    await sendKey("Escape");
    expect(document.querySelector(".rrugc-blueprint-detail-dialog")).toBeNull();
    await act(async () => root.unmount());
  });

  it("keeps both selection positions centered while arrow keys browse all designs and colors", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    expect(chosen(host)).toContain("design_9.png");
    expect(chosen(host)).toContain("Natural / Black");
    await sendKey("ArrowLeft");
    expect(chosen(host)).toContain("design_9.png");
    await sendKey("ArrowRight");
    expect(chosen(host)).toContain("design_8.png");
    await sendKey("ArrowDown");
    expect(chosen(host)).toContain("Natural / Brown");
    await sendKey("ArrowUp");
    expect(chosen(host)).toContain("Natural / Black");
    await sendKey("ArrowUp");
    expect(chosen(host)).toContain("Natural / Black");
    for (let i = 0; i < 12; i++) await sendKey("ArrowDown");
    expect(chosen(host)).toContain("Khaki / Maroon");
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Next hat color"]')?.disabled).toBe(true);
    const preview = host.querySelector(".rrugc-blueprint-selected-card")!;
    expect(preview).not.toBeNull();
    expect(host.querySelectorAll(".rrugc-blueprint-selected-card")).toHaveLength(1);
    await act(async () => root.unmount());
  });

  it("slides only the horizontal strip on horizontal drags and only the vertical strip on vertical drags", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const horizontal = host.querySelector(".rrugc-blueprint-design-track")!;
    const vertical = host.querySelector(".rrugc-blueprint-color-track")!;
    await drag(horizontal, 300, 80, 135, 80);
    expect(chosen(host)).toContain("design_8.png");
    expect(chosen(host)).toContain("Natural / Black");
    await drag(vertical, 70, 250, 70, 158);
    expect(chosen(host)).toContain("design_8.png");
    expect(chosen(host)).toContain("Natural / Brown");
    await drag(vertical, 70, 140, 70, 245);
    expect(chosen(host)).toContain("Natural / Black");
    // Main preview also accepts direction-sensitive mouse drag without moving the card itself.
    await drag(host.querySelector(".rrugc-blueprint-selected-card")!, 250, 90, 65, 90);
    expect(chosen(host)).toContain("design_7.png");
    await act(async () => root.unmount());
  });

  it("supports click navigation and active cards in a left-aligned, non-centered strip", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    await act(async () => click(host, "Select design design_7.png"));
    expect(chosen(host)).toContain("design_7.png");
    await act(async () => click(host, "Select hat color Natural / Brown"));
    expect(chosen(host)).toContain("Natural / Brown");
    expect(host.querySelectorAll(".rrugc-blueprint-thread-source")).toHaveLength(2);
    expect(host.querySelector(".rrugc-blueprint-thread-source")?.textContent).toBe("Auto-match thread");
    expect(host.querySelector('select[aria-label="Thread color mode"]')).toBeNull();
    expect(host.querySelectorAll('button[aria-label*="embroidery palette"]')).toHaveLength(0);
    expect(host.querySelector(".rrugc-blueprint-design-item.is-active")?.hasAttribute("style")).toBe(false);
    expect(host.querySelectorAll(".rrugc-blueprint-design-item")).toHaveLength(versions.length);
    expect(host.querySelector(".rrugc-blueprint-color-choice.is-selected")?.getAttribute("style")).toContain("translate(-50%, calc(-50% + 0px))");
    expect(host.querySelector<HTMLInputElement>('input[aria-label="Front design zoom"]')?.max).toBe("200");
    expect(host.querySelector<HTMLInputElement>('input[aria-label="Front design zoom"]')?.value).toBe("100");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-selected-design img")?.style.opacity).toBe("0");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.getAttribute("alt")).toContain("Natural / Brown");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.classList.contains("rrugc-blueprint-smooth-img")).toBe(true);
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.src).toContain("/rrugc/blueprint/fronts/natural-brown.webp");
    expect(host.querySelectorAll<HTMLImageElement>(".rrugc-blueprint-color-hat").length).toBeGreaterThanOrEqual(5);
    expect(host.querySelector<HTMLInputElement>('input[aria-label="Right side design zoom"]')?.max).toBe("200");
    await act(async () => root.render(<BlueprintPreview versions={versions} guides />));
    expect(host.querySelector(".rrugc-blueprint-selected-design")).not.toBeNull();
    expect(host.querySelector(".rrugc-blueprint-front-guide")).not.toBeNull();
    expect(host.querySelectorAll(".rrugc-blueprint-view-zoom")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-blueprint-front-height-guides")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-blueprint-height-line.is-upper")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-blueprint-height-line.is-lower")).toHaveLength(2);
    expect(host.querySelector(".rrugc-blueprint-front-height-guides[role=img]")?.getAttribute("aria-label")).toContain("Maximum height");
    expect(host.querySelectorAll(".rrugc-blueprint-side-guides")).toHaveLength(2);
    const sideGuides = host.querySelector<SVGSVGElement>(".rrugc-blueprint-side-image > .rrugc-blueprint-side-guides")!;
    expect(sideGuides.getAttribute("viewBox")).toBe("0 0 700 430");
    expect(sideGuides.querySelector(".rrugc-blueprint-side-hoop")).not.toBeNull();
    expect(sideGuides.querySelector(".rrugc-blueprint-side-sew-field")).not.toBeNull();
    expect(sideGuides.querySelector(".rrugc-blueprint-side-design-field")).not.toBeNull();
    expect(sideGuides.querySelectorAll("path")).toHaveLength(1);
    expect(sideGuides.querySelectorAll("rect")).toHaveLength(2);
    expect(sideGuides.querySelector("g")?.getAttribute("transform")).toBe(
      "translate(568 -18) scale(-1.06 0.845) rotate(9 303.86012554478054 362.66312948411974)"
    );
    expect(sideGuides.parentElement?.classList.contains("rrugc-blueprint-side-image")).toBe(true);
    expect(sideGuides.querySelector(".rrugc-blueprint-side-hoop")?.getAttribute("d")).toContain("208.53503011589822 423.61823217225503");
    expect(sideGuides.querySelector(".rrugc-blueprint-side-sew-field")?.getAttribute("width")).toBe("239.88383736723736");
    expect(sideGuides.querySelector(".rrugc-blueprint-side-design-field")?.getAttribute("width")).toBe("122.33387246706563");
    expect(sideGuides.querySelector(".rrugc-blueprint-side-design-field")?.getAttribute("height")).toBe("41.83712521600946");
    const calibration = host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-image > .rrugc-blueprint-side-calibration-design")!;
    expect(calibration.src).toContain("/rrugc/blueprint/roberts-reference.png");
    expect(calibration.style.getPropertyValue("--side-design-scale")).toBe("1");
    expect(sideGuides.querySelector(".rrugc-blueprint-side-guides")).toBeNull();
    expect(host.querySelectorAll(".rrugc-blueprint-side-guides g[transform]")).toHaveLength(2);
    expect(host.querySelector(".rrugc-blueprint-magnifier-side-surface .rrugc-blueprint-side-guides g")
      ?.getAttribute("transform")).toBe(sideGuides.querySelector("g")?.getAttribute("transform"));
    expect(host.querySelectorAll(".rrugc-blueprint-side-calibration-design")).toHaveLength(2);
    expect(host.querySelector(".rrugc-blueprint-magnifier-side-surface > .rrugc-blueprint-side-guides")).not.toBeNull();
    expect(host.querySelector<HTMLElement>(".rrugc-blueprint-side-image")?.getAttribute("aria-label")).toContain("Natural / Brown");
    expect(host.querySelector<HTMLElement>(".rrugc-blueprint-side-image > .rrugc-blueprint-side-content")?.style.backgroundPosition).toBe("50% 0%");
    await act(async () => root.unmount());
  });

  it("projects reference hoop, safe sew field and embroidery inside the 8869 right-side fabric", () => {
    const pivotX = 303.86012554478054;
    const pivotY = 362.66312948411974;
    const radians = 9 * Math.PI / 180;
    const project = ([x, y]: [number, number]): [number, number] => {
      const rotatedX = pivotX + (x - pivotX) * Math.cos(radians) - (y - pivotY) * Math.sin(radians);
      const rotatedY = pivotY + (x - pivotX) * Math.sin(radians) + (y - pivotY) * Math.cos(radians);
      return [568 - 1.06 * rotatedX, -18 + 0.845 * rotatedY];
    };
    const corners = (rect: { x: number; y: number; width: number; height: number }): [number, number][] => [
      [rect.x, rect.y], [rect.x + rect.width, rect.y],
      [rect.x + rect.width, rect.y + rect.height], [rect.x, rect.y + rect.height],
    ];
    const hoopNumbers = (RIGHT_SIDE_GUIDE.hoop.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
    const hoopPoints: [number, number][] = [];
    for (let i = 0; i < hoopNumbers.length; i += 2) hoopPoints.push([hoopNumbers[i], hoopNumbers[i + 1]]);
    const bounds = (points: [number, number][]) => {
      const converted = points.map(project);
      return {
        minX: Math.min(...converted.map(point => point[0])),
        maxX: Math.max(...converted.map(point => point[0])),
        minY: Math.min(...converted.map(point => point[1])),
        maxY: Math.max(...converted.map(point => point[1])),
      };
    };
    const hoop = bounds(hoopPoints);
    const sewing = bounds(corners(RIGHT_SIDE_GUIDE.sewField));
    const embroidery = bounds(corners(RIGHT_SIDE_GUIDE.designField));
    expect(RIGHT_SIDE_GUIDE.viewBox).toBe("0 0 700 430");
    expect(RIGHT_SIDE_GUIDE.transform).toContain("rotate(9 ");
    expect(hoop.minX).toBeGreaterThan(60);
    expect(hoop.maxX).toBeLessThan(430);
    expect(hoop.minY).toBeGreaterThan(190);
    expect(hoop.maxY).toBeLessThan(355); // never cross the bottom seam/brim
    for (const inner of [sewing, embroidery]) {
      expect(inner.minX).toBeGreaterThan(hoop.minX);
      expect(inner.maxX).toBeLessThan(hoop.maxX);
      expect(inner.minY).toBeGreaterThan(hoop.minY);
      expect(inner.maxY).toBeLessThan(hoop.maxY);
    }
    expect(embroidery.minX).toBeGreaterThan(sewing.minX);
    expect(embroidery.maxX).toBeLessThan(sewing.maxX);
    expect(embroidery.minY).toBeGreaterThan(sewing.minY);
    expect(embroidery.maxY).toBeLessThan(sewing.maxY);
  });

  it("keeps an unprocessed source fallback while the compositor is loading", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const source = () => host.querySelector<HTMLImageElement>(".rrugc-blueprint-selected-design img")?.getAttribute("src");
    expect(source()).toBe(versions[0].url + "?artwork=stage1-thread-geometry-v1");
    await act(async () => click(host, "Select hat color Natural / Brown"));
    expect(source()).toBe(versions[0].url + "?artwork=stage1-thread-geometry-v1");
    await act(async () => click(host, "Next design columns"));
    expect(source()).toBe(versions[1].url + "?artwork=stage1-thread-geometry-v1");
    expect(host.querySelectorAll(".rrugc-blueprint-thread-source")).toHaveLength(2);
    await act(async () => root.unmount());
  });

  it("scales only embroidery artwork with independent header sliders (not cap photos)", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const front = host.querySelector<HTMLElement>(".rrugc-blueprint-selected-hat")!;
    const side = host.querySelector<HTMLElement>(".rrugc-blueprint-side-image")!;
    const frontGroup = host.querySelector<HTMLElement>('[aria-label="Front design size"]')!;
    const sideGroup = host.querySelector<HTMLElement>('[aria-label="Right side design size"]')!;
    const frontSlider = frontGroup.querySelector<HTMLInputElement>('input[type="range"]')!;
    const sideSlider = sideGroup.querySelector<HTMLInputElement>('input[type="range"]')!;
    expect(frontGroup.closest("header.rrugc-blueprint-panel-header")).not.toBeNull();
    expect(sideGroup.closest("header.rrugc-blueprint-panel-header")).not.toBeNull();
    expect(host.querySelectorAll(".rrugc-blueprint-panel-visual .rrugc-blueprint-view-zoom")).toHaveLength(0);
    expect(host.querySelectorAll('.rrugc-blueprint-controls input[type="range"]')).toHaveLength(0);
    expect(frontSlider.value).toBe("100");
    expect(sideSlider.value).toBe("100");
    expect(front.style.transform).toBe("");
    expect(side.style.transform).toBe("");
    const sideEmbroidery = host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-image > img.rrugc-blueprint-side-calibration-design")!;
    const lensEmbroidery = host.querySelector<HTMLImageElement>(".rrugc-blueprint-magnifier-side-surface > img.rrugc-blueprint-side-calibration-design")!;
    expect(lensEmbroidery.getAttribute("src")).toBe(sideEmbroidery.getAttribute("src"));
    expect(lensEmbroidery.style.getPropertyValue("--side-design-scale")).toBe(sideEmbroidery.style.getPropertyValue("--side-design-scale"));
    expect(sideEmbroidery.style.getPropertyValue("--side-design-scale")).toBe("1");
    const change = async (slider: HTMLInputElement, number: number) => {
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
        setter.call(slider, String(number));
        slider.dispatchEvent(new Event("input", { bubbles: true }));
        slider.dispatchEvent(new Event("change", { bubbles: true }));
      });
    };
    await change(frontSlider, 150);
    expect(frontSlider.value).toBe("150");
    expect(sideSlider.value).toBe("100");
    expect(frontGroup.textContent).toContain("150%");
    expect(sideGroup.textContent).toContain("100%");
    expect(front.style.transform).toBe("");
    expect(side.style.transform).toBe("");
    await change(sideSlider, 175);
    expect(sideSlider.value).toBe("175");
    expect(frontSlider.value).toBe("150");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-image > .rrugc-blueprint-side-calibration-design")?.style.getPropertyValue("--side-design-scale")).toBe("1.75");
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-magnifier-side-surface > .rrugc-blueprint-side-calibration-design")?.style.getPropertyValue("--side-design-scale")).toBe("1.75");
    expect(chosen(host)).toContain("design_9.png");
    await act(async () => root.unmount());
  });

  it("shows a circular hover lens for both Front and Side, then hides on drag or leave", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const front = host.querySelector<HTMLElement>(".rrugc-blueprint-selected-hat")!;
    const frontLens = front.querySelector<HTMLElement>(".rrugc-blueprint-magnifier")!;
    expect(frontLens.getAttribute("aria-hidden")).toBe("true");
    for (const img of front.querySelectorAll<HTMLImageElement>(".rrugc-blueprint-smooth-img")) img.classList.add("is-loaded");
    const setupBounds = (node: HTMLElement) => {
      Object.defineProperty(node, "clientWidth", { configurable: true, value: 400 });
      Object.defineProperty(node, "clientHeight", { configurable: true, value: 500 });
      node.getBoundingClientRect = () => ({ left: 0, top: 0, right: 400, bottom: 500, width: 400, height: 500 } as DOMRect);
    };
    const dispatch = (node: HTMLElement, type: string, buttons = 0, pointerType = "mouse") => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperties(event, {
        clientX: { value: 200 }, clientY: { value: 250 },
        buttons: { value: buttons }, pointerType: { value: pointerType },
      });
      node.dispatchEvent(event);
    };
    setupBounds(front);
    await act(async () => dispatch(front, "pointermove"));
    expect(frontLens.style.opacity).toBe("1");
    expect(frontLens.style.left).toBe("118px");
    expect(frontLens.querySelector(".rrugc-blueprint-selected-design img")).not.toBeNull();
    await act(async () => dispatch(front, "pointermove", 1));
    expect(frontLens.style.opacity).toBe("0");
    await act(async () => dispatch(front, "pointermove"));
    await act(async () => dispatch(front, "pointerout"));
    expect(frontLens.style.opacity).toBe("0");

    const probe = host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-probe")!;
    await act(async () => probe.dispatchEvent(new Event("load")));
    const side = host.querySelector<HTMLElement>(".rrugc-blueprint-side-image")!;
    const sideLens = side.querySelector<HTMLElement>(".rrugc-blueprint-magnifier")!;
    setupBounds(side);
    await act(async () => dispatch(side, "pointermove"));
    expect(sideLens.style.opacity).toBe("1");
    expect(sideLens.querySelector<HTMLElement>(".rrugc-blueprint-magnifier-side-surface > .rrugc-blueprint-side-content")?.style.backgroundPosition).toBe("0% 0%");
    await act(async () => click(host, "Select hat color Natural / Brown"));
    expect(sideLens.style.opacity).toBe("0");
    await act(async () => root.unmount());
  });

  it("loads the twelve Side views from one atlas and switches the tile without refetching", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const probe = host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-probe")!;
    expect(probe.src).toContain("/rrugc/blueprint/sides-atlas-fit.webp");
    await act(async () => probe.dispatchEvent(new Event("load")));
    expect(host.querySelector(".rrugc-blueprint-side-image.is-ready")).not.toBeNull();
    await act(async () => click(host, "Select hat color Natural / Brown"));
    expect(host.querySelector<HTMLElement>(".rrugc-blueprint-side-image > .rrugc-blueprint-side-content")?.style.backgroundPosition).toBe("50% 0%");
    expect(host.querySelectorAll(".rrugc-blueprint-side-probe")).toHaveLength(1);
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-probe")?.src).toBe(probe.src);
    expect(host.querySelectorAll(".rrugc-blueprint-panel-header")).toHaveLength(2);
    expect(host.querySelectorAll(".rrugc-blueprint-panel-footer")).toHaveLength(0);
    expect(host.querySelectorAll(".rrugc-blueprint-panel-visual")).toHaveLength(2);
    await act(async () => root.unmount());
  });

  it("shows a clear Side fallback when an image is missing without blocking navigation", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    await act(async () => host.querySelector<HTMLImageElement>(".rrugc-blueprint-side-image img")!.dispatchEvent(new Event("error")));
    expect(host.querySelector(".rrugc-blueprint-side-unavailable")?.textContent).toContain("could not be loaded");
    await act(async () => click(host, "Select hat color Natural / Brown"));
    expect(host.querySelector(".rrugc-blueprint-side-unavailable")).not.toBeNull();
    expect(host.querySelector<HTMLImageElement>(".rrugc-blueprint-hat-original")?.src).toContain("/rrugc/blueprint/fronts/natural-brown.webp");
    await act(async () => root.unmount());
  });

  it("starts at an explicitly selected version, keeps the full left-aligned strip mounted, and clamps the boundaries", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} initialVersion={5} />));
    expect(chosen(host)).toContain("design_5.png");
    expect(host.querySelectorAll(".rrugc-blueprint-design-choice")).toHaveLength(versions.length);
    await act(async () => click(host, "Next design columns"));
    expect(chosen(host)).toContain("design_4.png");
    for (let i=0; i<9; i++) await sendKey("ArrowRight");
    expect(chosen(host)).toContain("design_1.png");
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Next design columns"]')?.disabled).toBe(true);
    expect(host.querySelectorAll(".rrugc-blueprint-design-choice")).toHaveLength(versions.length);
    await act(async () => root.unmount());
  });

  it("preserves the navigation helper bounds and handles empty galleries", () => {
    expect(blueprintWindow(versions, 99).offset).toBe(4);
    expect(BLUEPRINT_SIDE_ATLAS).toBe("/rrugc/blueprint/sides-atlas-fit.webp");
    expect(BLUEPRINT_SIDE_CALIBRATION_DESIGN).toBe("/rrugc/blueprint/roberts-reference.png");
    expect(blueprintSidePosition(0).backgroundPosition).toBe("0% 0%");
    expect(blueprintSidePosition(1).backgroundPosition).toBe("50% 0%");
    expect(blueprintSidePosition(3).backgroundPosition).toBe("0% 33.333333333333336%");
    expect(blueprintSidePosition(11).backgroundPosition).toBe("100% 100%");
    expect(blueprintSidePosition(12).backgroundImage).toContain("khaki-maroon.jpg");
    expect(blueprintNavigate(0, 0, "left", 9)).toEqual({ design: 0, color: 0 });
    expect(blueprintNavigate(8, 12, "down", 9)).toEqual({ design: 8, color: 12 });
    expect(renderToStaticMarkup(<BlueprintPreview versions={[]} />)).toContain("No designs available");
  });
});