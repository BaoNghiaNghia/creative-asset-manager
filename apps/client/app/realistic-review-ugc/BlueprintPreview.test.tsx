// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { BlueprintPreview, BLUEPRINT_HAT_COLORS, blueprintNavigate, blueprintWindow } from "./BlueprintPreview";
import type { GenerationOutputVersion } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const versions: GenerationOutputVersion[] = Array.from({ length: 7 }, (_, i) => ({
  version: 7 - i,
  output_name: "design_" + (7 - i) + ".png",
  url: "/api/v1/outputs/" + (7 - i),
  created_at: "2026-10-09T00:00:00Z",
  width: 1000, height: 1000,
}));

afterEach(() => document.body.replaceChildren());

function activeCell(host: HTMLElement) {
  return host.querySelector<HTMLElement>(".rrugc-blueprint-cell.is-active-cell")!;
}

function click(host: HTMLElement, label: string) {
  host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)?.click();
}

describe("Stage 1 Blueprint puzzle matrix", () => {
  it("renders real 12-color mockups across a five-design horizontal strip with one cyan focus corridor", () => {
    expect(BLUEPRINT_HAT_COLORS).toHaveLength(12);
    expect(new Set(BLUEPRINT_HAT_COLORS.map(color => color.id)).size).toBe(12);
    const html = renderToStaticMarkup(<BlueprintPreview versions={versions} />);
    expect(html).toContain("12 colors × 7 designs");
    expect(html).toContain("No AI generation required");
    expect(html).toContain("8869 Twill Cap");
    expect((html.match(/rrugc-blueprint-cell/g) || []).length).toBeGreaterThanOrEqual(60);
    expect((html.match(/class="rrugc-blueprint-color(?: is-active-row)?"/g) || []).length).toBe(12);
    expect(html).toContain("Natural / Realtree");
    expect(html).toContain("Natural / Maroon");
    expect(html).toContain("DESIGN 1 · SELECTED");
    expect(html).toContain("design_7.png");
    expect(html).toContain("design_3.png");
    expect(html).not.toContain("design_2.png");
    expect((html.match(/class="rrugc-blueprint-cell[^"]*is-active-column/g) || []).length).toBe(12);
    expect((html.match(/is-active-cell/g) || []).length).toBe(1);
    expect((html.match(/aria-pressed="true"/g) || []).length).toBe(2); // Heading and focused cell.
  });

  it("moves one focused design column left/right and hat color up/down with arrow keys", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Black · design_7.png");
    const key = (value: string) => act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true }));
    });
    await key("ArrowLeft"); // Clamp first design.
    expect(activeCell(host).getAttribute("aria-label")).toContain("design_7.png");
    await key("ArrowRight");
    expect(activeCell(host).getAttribute("aria-label")).toContain("design_6.png");
    expect(host.querySelectorAll(".rrugc-blueprint-cell.is-active-column")).toHaveLength(12);
    await key("ArrowDown");
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Brown");
    await key("ArrowUp");
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Black");
    await key("ArrowUp"); // Clamp first color.
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Black");
    for (let index = 0; index < 11; index++) await key("ArrowDown");
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Royal");
    await key("ArrowDown"); // Clamp last color.
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Royal");
    expect(host.querySelector<HTMLButtonElement>('button[aria-label="Next hat color"]')?.disabled).toBe(true);
    await act(async () => root.unmount());
  });

  it("moves the highlighted column by dragging horizontally and the hat selection by dragging vertically", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const viewport = host.querySelector<HTMLElement>(".rrugc-blueprint-viewport")!;
    const drag = (startX: number, startY: number, endX: number, endY: number) => act(async () => {
      const pointer = (type: string, x: number, y: number) => {
        const event = new Event(type, { bubbles: true });
        Object.defineProperties(event, {
          button: { value: 0 }, buttons: { value: type === "pointerup" ? 0 : 1 },
          pointerType: { value: "mouse" }, pointerId: { value: 1 },
          clientX: { value: x }, clientY: { value: y },
        });
        viewport.dispatchEvent(event);
      };
      pointer("pointerdown", startX, startY);
      pointer("pointerup", endX, endY);
    });
    await drag(310, 100, 125, 100);
    expect(activeCell(host).getAttribute("aria-label")).toContain("design_6.png");
    await drag(100, 100, 100, 300);
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Black"); // Upward stays on first.
    await drag(100, 300, 100, 90);
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Brown");
    await act(async () => root.unmount());
  });

  it("lets users click another column or cell without changing saved designs", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    await act(async () => click(host, "Select design design_4.png"));
    expect(activeCell(host).getAttribute("aria-label")).toContain("design_4.png");
    await act(async () => click(host, "Natural / Navy · design_4.png"));
    expect(activeCell(host).getAttribute("aria-label")).toContain("Natural / Navy · design_4.png");
    await act(async () => click(host, "Next design columns"));
    expect(activeCell(host).getAttribute("aria-label")).toContain("design_3.png");
    await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click());
    expect(host.querySelectorAll(".rrugc-blueprint-design-zone.is-guided")).toHaveLength(60);
    await act(async () => root.unmount());
  });

  it("centers the five-design window and clamps navigation boundaries", () => {
    expect(blueprintWindow(versions, 999).offset).toBe(2);
    expect(blueprintWindow(versions, -1).offset).toBe(0);
    expect(blueprintWindow(versions, 3).items.map(item => item.version)).toEqual([6, 5, 4, 3, 2]);
    expect(blueprintWindow([], 0).items).toEqual([]);
    expect(blueprintNavigate(0, 0, "left", 7)).toEqual({ design: 0, color: 0 });
    expect(blueprintNavigate(0, 0, "up", 7)).toEqual({ design: 0, color: 0 });
    expect(blueprintNavigate(6, 11, "down", 7)).toEqual({ design: 6, color: 11 });
    expect(blueprintNavigate(5, 10, "right", 7)).toEqual({ design: 6, color: 10 });
  });
});
