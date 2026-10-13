// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { Stage5BeforeAfter } from "./Stage5BeforeAfter";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => document.body.replaceChildren());

describe("Stage 5 Before / After comparison", () => {
  it("loads the saved Pinterest scene and toggles Before, After and draggable Compare", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => root.render(<Stage5BeforeAfter
      beforeUrl="/api/stage4/job-1/reference-image"
      afterUrl="/api/stage4/job-1/output"
      originalUrl="/api/stage4/job-1/output-original"
      alt="UGC after photo"
    />));
    const stage = host.querySelector<HTMLElement>(".rrugc-stage5-ba")!;
    const button = (name: string) => [...host.querySelectorAll<HTMLButtonElement>(".rrugc-stage5-ba-switch button")]
      .find(node => node.textContent === name)!;
    expect(stage.classList.contains("is-after")).toBe(true);
    expect(button("After").getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector<HTMLImageElement>(".rrugc-stage5-ba-before")?.getAttribute("src"))
      .toBe("/api/stage4/job-1/reference-image");
    await act(async () => host.querySelector(".rrugc-stage5-ba-before")!.dispatchEvent(new Event("load")));
    await act(async () => button("Before").click());
    expect(stage.classList.contains("is-before")).toBe(true);
    await act(async () => button("Compare").click());
    expect(stage.classList.contains("is-compare")).toBe(true);
    const slider = host.querySelector<HTMLInputElement>(".rrugc-stage5-ba-range")!;
    expect(slider.value).toBe("50");
    expect(slider.getAttribute("aria-label")).toContain("comparison position");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(slider, "76");
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(stage.style.getPropertyValue("--stage5-before-split")).toBe("76%");
    await act(async () => root.unmount());
  });

  it("allows choosing among multiple Pinterest references for the same generated image", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => root.render(<Stage5BeforeAfter
      beforeUrl="/unused-product-hat"
      beforeUrls={["/saved-pinterest-1", "/saved-pinterest-2"]}
      afterUrl="/generated-ugc" originalUrl="/generated-ugc" alt="After replacement"
    />));
    const selector = host.querySelector<HTMLSelectElement>(
      'select[aria-label="Pinterest reference to compare"]'
    )!;
    expect(selector).not.toBeNull();
    expect(selector.options).toHaveLength(2);
    expect(host.querySelector<HTMLImageElement>(".rrugc-stage5-ba-before")?.getAttribute("src"))
      .toBe("/saved-pinterest-1");
    await act(async () => {
      selector.value = "1";
      selector.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(host.querySelector<HTMLImageElement>(".rrugc-stage5-ba-before")?.getAttribute("src"))
      .toBe("/saved-pinterest-2");
    expect(host.querySelector(".rrugc-stage5-ba-before")?.getAttribute("alt"))
      .toContain("Pinterest lifestyle reference 2");
    await act(async () => root.unmount());
  });

  it("disables missing before without showing an incorrect image", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => root.render(<Stage5BeforeAfter beforeUrl={null}
      afterUrl="/stage4/after.png" originalUrl="/stage4/original.png" alt="After" />));
    const controls = host.querySelectorAll<HTMLButtonElement>(".rrugc-stage5-ba-switch button");
    expect(controls[0].disabled).toBe(false);
    expect(controls[1].disabled).toBe(true);
    expect(controls[2].disabled).toBe(true);
    expect(host.textContent).toContain("Saved Pinterest reference unavailable");
    await act(async () => root.unmount());
  });

  it("falls back to After if the input image cannot be loaded", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => root.render(<Stage5BeforeAfter beforeUrl="/missing-hat"
      afterUrl="/after" originalUrl="/after-full" alt="Generated" />));
    await act(async () => host.querySelector(".rrugc-stage5-ba-before")!.dispatchEvent(new Event("error")));
    expect(host.querySelector(".rrugc-stage5-ba.is-after")).not.toBeNull();
    expect(host.textContent).toContain("could not be loaded");
    await act(async () => root.unmount());
  });
});
