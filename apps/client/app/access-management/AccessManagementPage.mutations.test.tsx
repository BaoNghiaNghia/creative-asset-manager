// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DangerousAction } from "./AccessManagementPage";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  document.body.replaceChildren();
});

describe("Access Management mutation guards", () => {
  it("submits a dangerous mutation only once while the first request is pending", async () => {
    let resolve!: () => void;
    const pending = new Promise<void>(done => { resolve = done; });
    const onConfirm = vi.fn(() => pending);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<DangerousAction label="Remove" title="Remove role" description="Remove this role?" onConfirm={onConfirm}/>);
    });

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[data-action="remove"]')!.click();
    });

    const input = host.querySelector<HTMLInputElement>('input')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "approved removal");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const confirm = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find(button => button.textContent?.includes("Confirm remove"))!;
    await act(async () => {
      confirm.click();
      confirm.click();
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find(button => button.textContent === "Working…")?.disabled).toBe(true);

    await act(async () => {
      resolve();
      await pending;
    });
    await act(async () => root.unmount());
  });
});
