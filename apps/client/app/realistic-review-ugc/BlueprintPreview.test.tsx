// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { BlueprintPreview, BLUEPRINT_HAT_COLORS, blueprintWindow } from "./BlueprintPreview";
import type { GenerationOutputVersion } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const versions: GenerationOutputVersion[] = Array.from({ length: 7 }, (_, i) => ({
  version: 7 - i,
  output_name: "design_" + (7 - i) + ".png",
  url: "/api/v1/outputs/" + (7 - i),
  created_at: "2026-10-09T00:00:00Z",
  width: 1000,
  height: 1000,
}));

afterEach(() => document.body.replaceChildren());

describe("Stage 1 Blueprint hat design matrix", () => {
  it("uses exactly the 12 user-supplied colorways and four design columns", () => {
    expect(BLUEPRINT_HAT_COLORS).toHaveLength(12);
    expect(new Set(BLUEPRINT_HAT_COLORS.map(color => color.id)).size).toBe(12);
    const html = renderToStaticMarkup(<BlueprintPreview versions={versions} />);
    expect(html).toContain("12 hat colors × 7 designs");
    expect(html).toContain("No AI generation required");
    expect(html).not.toContain("8869-hat-atlas.svg"); // The shared CSS renders the sprite.
    expect((html.match(/class="rrugc-blueprint-cell"/g) || []).length).toBe(48);
    expect((html.match(/class="rrugc-blueprint-color"/g) || []).length).toBe(12);
    expect(html).toContain("Natural / Realtree");
    expect(html).toContain("Natural / Maroon");
  });

  it("navigates designs horizontally without changing the 12 color rows", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<BlueprintPreview versions={versions} />));
    const first = host.querySelector<HTMLButtonElement>('button[aria-label="Previous design columns"]')!;
    const next = host.querySelector<HTMLButtonElement>('button[aria-label="Next design columns"]')!;
    expect(first.disabled).toBe(true);
    expect(next.disabled).toBe(false);
    expect(host.querySelectorAll(".rrugc-blueprint-cell")).toHaveLength(48);
    await act(async () => next.click());
    expect(first.disabled).toBe(false);
    expect(host.querySelector(".rrugc-blueprint-nav")?.textContent).toContain("2–5 / 7");
    const matrix = host.querySelector<HTMLElement>(".rrugc-blueprint-viewport")!;
    await act(async () => matrix.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(host.querySelector(".rrugc-blueprint-nav")?.textContent).toContain("3–6 / 7");
    await act(async () => next.click());
    expect(host.querySelector(".rrugc-blueprint-nav")?.textContent).toContain("4–7 / 7");
    expect(next.disabled).toBe(true);
    expect(host.querySelectorAll(".rrugc-blueprint-color")).toHaveLength(12);
    await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click());
    expect(host.querySelectorAll(".rrugc-blueprint-design-zone.is-guided")).toHaveLength(48);
    await act(async () => root.unmount());
  });

  it("clamps the selected initial design to a visible four-column window", () => {
    expect(blueprintWindow(versions, 999).offset).toBe(3);
    expect(blueprintWindow(versions, -1).offset).toBe(0);
    expect(blueprintWindow(versions, 2).items.map(item => item.version)).toEqual([5, 4, 3, 2]);
  });
});
