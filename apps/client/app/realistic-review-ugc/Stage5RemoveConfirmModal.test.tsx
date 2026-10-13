// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Stage5RemoveConfirmModal } from "./Stage3ReviewGroups";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  document.body.replaceChildren();
  document.body.style.overflow = "";
});

describe("Stage 5 remove review confirmation", () => {
  it("keeps Remove review on one line, uses a bounded icon and explains image preservation", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <Stage5RemoveConfirmModal busy={false} onCancel={() => undefined} onConfirm={() => undefined} />,
    ));
    const dialog = document.querySelector('[role="dialog"][aria-labelledby="stage5-delete-confirm-title"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain("Stage 4");
    expect(dialog?.textContent).toContain("future exports");
    const confirm = dialog?.querySelector<HTMLButtonElement>(".rrugc-stage5-confirm-delete");
    expect(confirm?.textContent).toBe("Remove review");
    expect(confirm?.querySelector('svg[data-ui-icon="trash"]')).not.toBeNull();
    expect(dialog?.querySelector(".rrugc-stage5-confirm-icon")).not.toBeNull();
    expect(document.activeElement?.textContent).toBe("Cancel");
    await act(async () => root.unmount());
  });

  it("closes via Escape and the backdrop, disables actions while removing and preserves focus", async () => {
    const trigger = document.createElement("button");
    const host = document.createElement("div");
    document.body.append(trigger, host);
    trigger.focus();
    const root = createRoot(host);
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    await act(async () => root.render(
      <Stage5RemoveConfirmModal busy={false} onCancel={onCancel} onConfirm={onConfirm} />,
    ));
    const backdrop = document.querySelector(".rrugc-stage5-confirm-backdrop")!;
    const dialog = backdrop.querySelector(".rrugc-stage5-confirm")!;
    await act(async () => dialog.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(onCancel).not.toHaveBeenCalled();
    await act(async () => backdrop.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(onCancel).toHaveBeenCalledTimes(1);
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(onCancel).toHaveBeenCalledTimes(2);
    await act(async () => (dialog.querySelector(".rrugc-stage5-confirm-delete") as HTMLButtonElement).click());
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await act(async () => root.render(
      <Stage5RemoveConfirmModal busy onCancel={onCancel} onConfirm={onConfirm} />,
    ));
    expect((dialog.querySelector(".rrugc-stage5-confirm-delete") as HTMLButtonElement).disabled).toBe(true);
    expect((dialog.querySelector(".rrugc-stage5-confirm-cancel") as HTMLButtonElement).disabled).toBe(true);
    expect(dialog.textContent).toContain("Removing…");
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(onCancel).toHaveBeenCalledTimes(2);
    await act(async () => root.unmount());
    expect(document.activeElement).toBe(trigger);
  });
});
