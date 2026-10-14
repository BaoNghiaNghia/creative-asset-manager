// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodexAccountManager } from "./CodexAccountManager";
import { getCodexAccounts, updateCodexAccount } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", async importOriginal => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual,
    getCodexAccounts: vi.fn(async () => ({
      routing_mode: "single_active",
      routing_note: "Account 01 handles jobs while Account 02 is not connected.",
      can_manage: true,
      accounts: [
        { account_id: "account-1", display_name: "Existing", enabled: true,
          max_concurrent: 1, stages: ["stage1", "stage2", "stage4"],
          auth_status: "connected", is_executing: true, active_jobs: 1, updated_at: "2026-10-14" },
        { account_id: "account-2", display_name: "Secondary", enabled: false,
          max_concurrent: 1, stages: ["stage1", "stage2", "stage4"],
          auth_status: "not_connected", is_executing: false, active_jobs: 0, updated_at: "2026-10-14" },
      ],
    })),
    updateCodexAccount: vi.fn(async () => ({
      routing_mode: "single_active", routing_note: "Account 01 remains active.",
      can_manage: true, accounts: [],
    })),
  };
});
afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
});

describe("Codex account manager", () => {
  it("renders pool availability and live worker slots", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<CodexAccountManager onClose={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('[role="dialog"][aria-modal="true"]')).not.toBeNull();
    expect(host.textContent).toContain("Account 01 · Existing");
    expect(host.textContent).toContain("Account 02 · Secondary");
    expect(host.textContent).toContain("Routing: Primary account");
    expect(host.textContent).toContain("1 running / 1 slot");
    const guides = host.querySelectorAll<HTMLButtonElement>("button");
    const guide = [...guides].find(item => item.textContent?.trim() === "Connection guide");
    await act(async () => guide?.click());
    expect(host.textContent).toContain("scripts/cam-codex-runtime.sh login");
    await act(async () => root.unmount());
  });
  it("only submits settings through an explicit Save action", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<CodexAccountManager onClose={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const inputs = host.querySelectorAll<HTMLInputElement>('input[type="text"]');
    await act(async () => {
      const input = inputs[1];
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, "value",
      )!.set!;
      setter.call(input, "My other Codex");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(updateCodexAccount).not.toHaveBeenCalled();
    const saves = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .filter(button => button.textContent?.includes("Save configuration"));
    await act(async () => { saves[1].click(); await Promise.resolve(); });
    expect(updateCodexAccount).toHaveBeenCalledTimes(1);
    expect(updateCodexAccount).toHaveBeenCalledWith(
      "account-2", expect.objectContaining({ max_concurrent: 1 }),
    );
    await act(async () => root.unmount());
  });
});
