// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createScoutAgent, listScoutAgents } from "../realistic-review-ugc/api";
import { TiktokShopSettingsModal } from "./TiktokShopSettingsModal";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../realistic-review-ugc/api", () => ({
  listScoutAgents: vi.fn(async () => [{
    id: "agent-tiktok", name: "TikTokShop Windows Scout", status: "offline",
    active: true, machine_label: "Windows Scout",
  }]),
  createScoutAgent: vi.fn(async () => ({
    id: "agent-new", agent_token: "one-time-secret",
  })),
}));

beforeEach(() => vi.clearAllMocks());
afterEach(() => { document.body.replaceChildren(); document.body.style.overflow = ""; });

describe("TikTokShop header Settings modal", () => {
  it("does not fetch agents or render pairing cards while closed", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(
      <TiktokShopSettingsModal open={false} onClose={() => undefined} />,
    ));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(listScoutAgents).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });

  it("groups agent pairing and Stage 3 source rules, isolated to TikTokShop", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onClose = vi.fn();
    await act(async () => root.render(
      <TiktokShopSettingsModal open onClose={onClose} />,
    ));
    const dialog = host.querySelector('[role="dialog"]');
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    expect(dialog?.textContent).toContain("Windows · TikTokShop Scout");
    expect(dialog?.textContent).toContain("Stage 3 · TikTokShop Drive");
    expect(dialog?.textContent).toContain("at most 12 product-context analyses");
    expect(listScoutAgents).toHaveBeenCalledWith(expect.any(AbortSignal), "tiktokshop");
    expect(createScoutAgent).not.toHaveBeenCalled();
    expect(document.body.style.overflow).toBe("hidden");

    const pair = [...host.querySelectorAll("button")]
      .find(button => button.textContent?.includes("Pair TikTokShop agent"));
    expect(pair).toBeDefined();
    await act(async () => pair?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(createScoutAgent).toHaveBeenCalledWith("TikTokShop Windows Scout", "tiktokshop");
    expect((host.querySelector('textarea[aria-label="TikTokShop Scout pairing settings"]') as HTMLTextAreaElement)?.value)
      .toContain("RRUGC_TIKTOKSHOP_SCOUT_TOKEN=one-time-secret");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => root.render(
      <TiktokShopSettingsModal open={false} onClose={onClose} />,
    ));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.style.overflow).toBe("");
    await act(async () => root.unmount());
  });
});
