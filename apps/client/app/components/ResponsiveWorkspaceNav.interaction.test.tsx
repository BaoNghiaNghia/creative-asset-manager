// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../features/access_management", () => ({
  fetchAccessIdentity: vi.fn().mockResolvedValue({
    permissions: ["inventory.read", "ai_operations.read", "realistic_review_ugc.read", "public_review.read", "tenant_members.read"],
  }),
}));

import { ResponsiveWorkspaceNav } from "./ResponsiveWorkspaceNav";

describe("mobile workspace overflow interactions", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    // React act support is intentionally declared for this browser-like test.
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<ResponsiveWorkspaceNav active="queue" />); });
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    host.remove();
  });

  it("opens a labeled menu for remaining routes and closes with Escape", async () => {
    const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="More workspaces"]');
    expect(trigger).not.toBeNull();
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    await act(async () => { trigger?.click(); });
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    const dialog = host.querySelector('[role="dialog"][aria-modal="true"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.querySelector('a[href="/settings/access"]')).not.toBeNull();
    expect(dialog?.querySelector('a[href="/job-queue"]')?.getAttribute("aria-current")).toBe("page");
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
  });



  it("opens GatorHats Products and Reviews from the phone UGC tab", async () => {
    const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="Choose GatorHats Products or Reviews"]');
    expect(trigger).not.toBeNull();
    await act(async () => { trigger?.click(); });
    const dialog = host.querySelector('#mobile-rrugc-workflow-dialog');
    expect(dialog).not.toBeNull();
    expect(dialog?.querySelector('a[href="/realistic-review-ugc?workflow=products&stage=stage0"]')).not.toBeNull();
    expect(dialog?.querySelector('a[href="/realistic-review-ugc?workflow=reviews&stage=reviews-stage2"]')).not.toBeNull();
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(host.querySelector('#mobile-rrugc-workflow-dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes when the backdrop is tapped", async () => {
    await act(async () => { host.querySelector<HTMLButtonElement>('button[aria-label="More workspaces"]')?.click(); });
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => {
      host.querySelector<HTMLButtonElement>('.mobile-workspace-more-scrim')?.click();
    });
    expect(host.querySelector('[role="dialog"]')).toBeNull();
  });
});
