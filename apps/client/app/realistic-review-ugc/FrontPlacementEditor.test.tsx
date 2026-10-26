// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FrontPlacementEditor } from "./FrontPlacementEditor";
import { BlueprintPreview } from "./BlueprintPreview";
import {
  DEFAULT_FRONT_PLACEMENT, dragFrontPlacement, frontPlacementChanged,
  frontPlacementStyle, type FrontPlacement,
} from "./FrontPlacement";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => document.body.replaceChildren());

function firePointer(target: Element, type: string, x: number, y: number) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    pointerId: { value: 3 }, pointerType: { value: "mouse" },
    button: { value: 0 }, buttons: { value: type === "pointerup" ? 0 : 1 },
    clientX: { value: x }, clientY: { value: y },
  });
  target.dispatchEvent(event);
}

describe("Front placement geometry", () => {
  it("preserves stock-photo coordinates across display zoom and scales CSS for all views", () => {
    const p = { ...DEFAULT_FRONT_PLACEMENT, left: 0.2, top: 0.24, upper_line: 0.15 };
    const css = frontPlacementStyle(p);
    expect(css["--rrugc-guide-left" as keyof typeof css]).toBe("20%");
    expect(css["--rrugc-guide-top" as keyof typeof css]).toBe("24%");
    expect(css["--rrugc-upper-line" as keyof typeof css]).toBe("15%");
  });

  it("moves the field, resizes in 8 directions and clamps it inside the original photo", () => {
    const origin = DEFAULT_FRONT_PLACEMENT;
    const moved = dragFrontPlacement(origin, "move", 0.08, 0.04);
    expect(moved.left).toBeCloseTo(0.232);
    expect(moved.top).toBeCloseTo(0.349);
    expect(dragFrontPlacement(origin, "move", 2, -3).left).toBeCloseTo(1 - origin.width);
    expect(dragFrontPlacement(origin, "move", 2, -3).top).toBe(0);
    expect(dragFrontPlacement(origin, "se", 0.03, 0.06).width).toBeCloseTo(origin.width + 0.03);
    expect(dragFrontPlacement(origin, "nw", 0.03, 0.02).width).toBeCloseTo(origin.width - 0.03);
    expect(dragFrontPlacement(origin, "n", 0, -9).top).toBe(0);
    expect(dragFrontPlacement(origin, "s", 0, 3).height).toBeCloseTo(1 - origin.top);
    expect(dragFrontPlacement(origin, "e", -2, 0).width).toBe(0.1);
    expect(dragFrontPlacement(origin, "w", 2, 0).width).toBeCloseTo(0.1);
  });

  it("moves both height guides independently and prevents them crossing", () => {
    const p = DEFAULT_FRONT_PLACEMENT;
    const top = dragFrontPlacement(p, "upper", 0, 0.05);
    expect(top.upper_line).toBeCloseTo(0.24);
    expect(top.lower_line).toBe(p.lower_line);
    const bottom = dragFrontPlacement(p, "lower", 0, -0.08);
    expect(bottom.upper_line).toBe(p.upper_line);
    expect(bottom.lower_line).toBeCloseTo(0.55);
    expect(dragFrontPlacement(p, "upper", 0, 20).upper_line).toBeCloseTo(p.lower_line - 0.04);
    expect(dragFrontPlacement(p, "lower", 0, -20).lower_line).toBeCloseTo(p.upper_line + 0.04);
    expect(frontPlacementChanged(top, p)).toBe(true);
    expect(frontPlacementChanged(p, p)).toBe(false);
  });

  it("supports pointer drag and keyboard adjustments without navigation", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    let current: FrontPlacement = { ...DEFAULT_FRONT_PLACEMENT };
    const onChange = vi.fn((next: FrontPlacement) => { current = next; });
    const render = () => <div className="rrugc-blueprint-front-scene">
      <FrontPlacementEditor value={current} onChange={onChange} />
    </div>;
    await act(async () => root.render(render()));
    const scene = host.querySelector<HTMLElement>(".rrugc-blueprint-front-scene")!;
    vi.spyOn(scene, "getBoundingClientRect").mockReturnValue({
      x: 0, y: 0, left: 0, top: 0, width: 500, height: 625,
      right: 500, bottom: 625, toJSON: () => ({}),
    });
    const field = host.querySelector<HTMLElement>(".rrugc-front-placement-field")!;
    await act(async () => {
      firePointer(field, "pointerdown", 100, 100);
      firePointer(field, "pointermove", 150, 150);
      firePointer(field, "pointerup", 150, 150);
    });
    expect(current.left).toBeCloseTo(DEFAULT_FRONT_PLACEMENT.left + .10);
    expect(current.top).toBeCloseTo(DEFAULT_FRONT_PLACEMENT.top + .08);
    await act(async () => root.render(render()));
    const line = host.querySelector<HTMLElement>(".rrugc-front-placement-height.is-upper")!;
    await act(async () => line.dispatchEvent(new KeyboardEvent("keydown", {
      key: "ArrowDown", bubbles: true, cancelable: true,
    })));
    expect(current.upper_line).toBeCloseTo(DEFAULT_FRONT_PLACEMENT.upper_line + .005);
    await act(async () => root.unmount());
    host.remove();
  });

  it("renders the editor in Stage1 Blueprint and saves without forcing navigation", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const saved = vi.fn(async (value: FrontPlacement) => ({ ...value, revision: 1 }));
    const versions = [{
      version: 1, created_at: "2026-10-26T00:00:00Z",
      url: "/api/v1/fake/1", width: 1000, height: 1000, output_name: "design_01.png",
    }];
    await act(async () => root.render(<BlueprintPreview versions={versions}
      placement={{ ...DEFAULT_FRONT_PLACEMENT }} placementReady guides
      onSavePlacement={saved} />));
    const editButton = Array.from(host.querySelectorAll("button"))
      .find(button => button.textContent?.includes("Edit placement"))!;
    expect(editButton).toBeTruthy();
    await act(async () => editButton.click());
    expect(host.querySelector(".rrugc-front-placement-field")).not.toBeNull();
    const lower = host.querySelector<HTMLElement>(".rrugc-front-placement-height.is-lower")!;
    await act(async () => lower.dispatchEvent(new KeyboardEvent("keydown", {
      key: "ArrowDown", bubbles: true, cancelable: true,
    })));
    const save = Array.from(host.querySelectorAll("button"))
      .find(button => button.textContent?.trim() === "Save")!;
    await act(async () => save.click());
    expect(saved).toHaveBeenCalledTimes(1);
    expect(saved.mock.calls[0][0].lower_line).toBeCloseTo(0.635);
    expect(host.querySelector(".rrugc-front-placement-field")).toBeNull();
    await act(async () => root.unmount());
    host.remove();
  });
});
