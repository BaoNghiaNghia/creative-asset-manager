// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useHorizontalDragScroll } from "./useHorizontalDragScroll";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function Harness({ onClick }: { onClick: () => void }) {
  const { dragging, dragHandlers } = useHorizontalDragScroll();
  return <div
    id="drag-track"
    className={dragging ? "is-dragging" : ""}
    {...dragHandlers}
  >
    <button id="drag-child" type="button" onClick={onClick}>Reference</button>
  </div>;
}

function pointerEvent(type: string, clientX: number) {
  const event = new MouseEvent(type, { bubbles: true, button: 0, clientX });
  Object.defineProperty(event, "pointerId", { value: 7 });
  Object.defineProperty(event, "pointerType", { value: "mouse" });
  return event;
}

async function mount(onClick: () => void) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<Harness onClick={onClick} />);
  });
}

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  container?.remove();
  container = null;
});

describe("useHorizontalDragScroll", () => {
  it("keeps a normal click when the pointer does not move past the drag threshold", async () => {
    const onClick = vi.fn();
    await mount(onClick);
    const child = document.getElementById("drag-child") as HTMLButtonElement;

    await act(async () => {
      child.dispatchEvent(pointerEvent("pointerdown", 120));
      child.dispatchEvent(pointerEvent("pointerup", 120));
      child.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("drags the track and suppresses the child click after a horizontal drag", async () => {
    const onClick = vi.fn();
    const setPointerCapture = vi.fn();
    const releasePointerCapture = vi.fn();
    const originalSetPointerCapture = HTMLElement.prototype.setPointerCapture;
    const originalReleasePointerCapture = HTMLElement.prototype.releasePointerCapture;
    const originalHasPointerCapture = HTMLElement.prototype.hasPointerCapture;

    Object.defineProperties(HTMLElement.prototype, {
      setPointerCapture: { configurable: true, value: setPointerCapture },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
      hasPointerCapture: { configurable: true, value: vi.fn(() => true) },
    });

    try {
      await mount(onClick);
      const track = document.getElementById("drag-track") as HTMLDivElement;
      const child = document.getElementById("drag-child") as HTMLButtonElement;
      track.scrollLeft = 100;

      await act(async () => {
        child.dispatchEvent(pointerEvent("pointerdown", 120));
        child.dispatchEvent(pointerEvent("pointermove", 90));
      });

      expect(track.scrollLeft).toBe(130);
      expect(track.classList.contains("is-dragging")).toBe(true);
      expect(setPointerCapture).toHaveBeenCalledWith(7);

      await act(async () => {
        child.dispatchEvent(pointerEvent("pointerup", 90));
        child.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      });

      expect(onClick).not.toHaveBeenCalled();
      expect(releasePointerCapture).toHaveBeenCalledWith(7);
      expect(track.classList.contains("is-dragging")).toBe(false);
    } finally {
      if (originalSetPointerCapture) HTMLElement.prototype.setPointerCapture = originalSetPointerCapture;
      else delete (HTMLElement.prototype as Partial<HTMLElement>).setPointerCapture;
      if (originalReleasePointerCapture) HTMLElement.prototype.releasePointerCapture = originalReleasePointerCapture;
      else delete (HTMLElement.prototype as Partial<HTMLElement>).releasePointerCapture;
      if (originalHasPointerCapture) HTMLElement.prototype.hasPointerCapture = originalHasPointerCapture;
      else delete (HTMLElement.prototype as Partial<HTMLElement>).hasPointerCapture;
    }
  });
});
