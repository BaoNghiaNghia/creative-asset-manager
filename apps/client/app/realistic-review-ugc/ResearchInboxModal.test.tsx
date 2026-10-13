// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResearchInboxPanel } from "./ResearchInboxPanel";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const status = {
  state: "ready", connected: true, server: "VPS", source: "Agent-Reach",
  last_seen_at: "2026-10-13T04:00:00Z", last_error_code: null,
  total_evidence: 72, pending_review: 1, stage0_auto_keywords: 24,
  platforms: { pinterest: 15, etsy: 9, amazon: 6 },
};

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.style.overflow = "";
  document.body.replaceChildren();
});

describe("Research Intelligence modal", () => {
  it("opens only on demand, loads stats and signals, restores focus and closes on Escape", async () => {
    const requested: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      requested.push(input);
      return { ok: true, json: async () => input.endsWith("/auto-discover/status")
        ? status
        : { items: [{
          id: "item-1", source: "etsy", platform: "etsy", channel: "listing",
          title: "Custom cap inspiration", excerpt: "A hat reference",
          url: "https://www.etsy.com/listing/123456789/cap",
          status: "pending", keyword: null, created_at: "2026-10-13T04:00:00Z",
        }] },
      };
    }));
    const host = document.createElement("div");
    const trigger = document.createElement("button");
    trigger.textContent = "Open Research Intelligence";
    document.body.append(trigger, host);
    trigger.focus();
    const root = createRoot(host);
    const onClose = vi.fn();

    await act(async () => root.render(<ResearchInboxPanel open={false} onClose={onClose} />));
    expect(document.querySelector('[role="dialog"][aria-label="Agent-Reach Research Intelligence"]')).toBeNull();
    expect(requested).toHaveLength(0);

    await act(async () => {
      root.render(<ResearchInboxPanel open onClose={onClose} />);
      await Promise.resolve();
    });
    const dialog = document.querySelector('[role="dialog"][aria-label="Agent-Reach Research Intelligence"]')!;
    expect(dialog).not.toBeNull();
    expect(dialog.textContent).toContain("Custom cap inspiration");
    expect(dialog.textContent).toContain("72");
    expect(dialog.textContent).toContain("24");
    expect(dialog.textContent).toContain("Pinterest");
    expect(dialog.textContent).toContain("Tạm dừng VPS");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Close Research Intelligence");
    expect(requested.some(path => path.endsWith("/signals?status=pending&limit=25"))).toBe(true);

    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => root.render(<ResearchInboxPanel open={false} onClose={onClose} />));
    expect(document.querySelector('[role="dialog"][aria-label="Agent-Reach Research Intelligence"]')).toBeNull();
    expect(document.body.style.overflow).toBe("");
    expect(document.activeElement).toBe(trigger);
    await act(async () => root.unmount());
  });

  it("closes only on backdrop and keeps JSON import available inside the dialog", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: string) => ({
      ok: true,
      json: async () => input.endsWith("/auto-discover/status") ? status : { items: [] },
    })));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onClose = vi.fn();
    await act(async () => { root.render(<ResearchInboxPanel open onClose={onClose} />); await Promise.resolve(); });
    const backdrop = document.querySelector(".rrugc-research-backdrop")!;
    await act(async () => backdrop.querySelector(".rrugc-research-inbox")!
      .dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(onClose).not.toHaveBeenCalled();
    const importButton = Array.from(backdrop.querySelectorAll("button")).find(button =>
      button.textContent?.includes("Nhập research JSON"))!;
    await act(async () => importButton.click());
    expect(backdrop.querySelector('textarea[placeholder]')).not.toBeNull();
    await act(async () => backdrop.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
  });
});
