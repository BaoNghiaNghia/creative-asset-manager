// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useActiveStageVisibility } from "./useActiveStageVisibility";

function Rail({ selected }: { selected: string }) {
  const ref = useActiveStageVisibility(selected);
  return <nav ref={ref} role="tablist">
    <button role="tab" aria-selected={selected === "one"}>One</button>
    <button role="tab" aria-selected={selected === "two"}>Two</button>
  </nav>;
}

afterEach(() => vi.unstubAllGlobals());

describe("horizontal stage visibility", () => {
  it("scrolls only the stage rail when selection is outside the viewport", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<Rail selected="one" />));
    const nav = container.querySelector("nav")!;
    const selected = container.querySelectorAll("button")[1];
    const rectangle = (left: number, right: number) => ({ left, right } as DOMRect);
    nav.getBoundingClientRect = () => rectangle(0, 300);
    selected.getBoundingClientRect = () => rectangle(345, 470);
    const railScroll = vi.fn();
    nav.scrollTo = railScroll;
    const pageScroll = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);

    await act(async () => root.render(<Rail selected="two" />));
    expect(railScroll).toHaveBeenCalledWith({ left: 182, behavior: "auto" });
    expect(pageScroll).not.toHaveBeenCalled();
    pageScroll.mockRestore();
    await act(async () => root.unmount());
  });
});
