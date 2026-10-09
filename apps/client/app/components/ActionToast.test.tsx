// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ActionMessageToast, ACTION_TOAST_DURATION_MS, ActionToastViewport, showActionToast } from "./ActionToast";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLElement;
let root: Root;

async function setup() {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<ActionToastViewport />));
}

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove();
  vi.useRealTimers();
});

describe("global action toast", () => {
  it("shows bottom-left feedback for five seconds and can be dismissed", async () => {
    vi.useFakeTimers();
    await setup();
    await act(async () => showActionToast("Generation queued for ALL I NEED IS A NAP AND A MILLION DOLLARS."));
    expect(host.querySelector(".cam-action-toasts")).not.toBeNull();
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(1);
    expect(host.textContent).toContain("Generation queued");
    await act(async () => vi.advanceTimersByTime(ACTION_TOAST_DURATION_MS - 1));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(1));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(0);
    await act(async () => showActionToast("Saved", "success"));
    await act(async () => host.querySelector<HTMLButtonElement>('button[aria-label="Dismiss notification"]')!.click());
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(0);
  });

  it("does not restart earlier toast timers when another action occurs", async () => {
    vi.useFakeTimers();
    await setup();
    await act(async () => showActionToast("First action"));
    await act(async () => vi.advanceTimersByTime(2_000));
    await act(async () => showActionToast("Second action", "error"));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(2);
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("Second action");
    await act(async () => vi.advanceTimersByTime(3_000));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(1);
    expect(host.textContent).not.toContain("First action");
    await act(async () => vi.advanceTimersByTime(2_000));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(0);
  });

  it("bridges legacy state messages without duplicating the initial event in StrictMode", async () => {
    vi.useFakeTimers();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    const { StrictMode } = await import("react");
    await act(async () => root.render(<><ActionToastViewport /><StrictMode><ActionMessageToast message="Queued" /></StrictMode></>));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(1);
    await act(async () => root.render(<><ActionToastViewport /><StrictMode><ActionMessageToast message="Saved" /></StrictMode></>));
    expect(host.querySelectorAll(".cam-action-toast")).toHaveLength(2);
  });
});
