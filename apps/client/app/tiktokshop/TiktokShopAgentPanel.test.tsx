// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { TiktokShopAgentPanel } from "./TiktokShopAgentPanel";
import { listScoutAgents } from "../realistic-review-ugc/api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../realistic-review-ugc/api", () => ({
  listScoutAgents: vi.fn(async () => [{
    id: "agent-1", name: "TikTokShop Windows Scout",
    workspace: "tiktokshop", status: "offline", active: true,
    last_error_code: "pinterest_startup_login_required",
    last_seen_at: "2026-11-01T11:27:19Z",
    machine_label: "DESKTOP-3FR0LF0", client_version: "rrugc-scout-v58-tiktokshop-isolation",
  }]),
  createScoutAgent: vi.fn(),
}));

afterEach(() => { document.body.replaceChildren(); });

it("makes the Pinterest login blocker visible even when the agent is offline", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<TiktokShopAgentPanel />));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(vi.mocked(listScoutAgents)).toHaveBeenCalledWith(
    expect.any(AbortSignal), "tiktokshop"
  );
  expect(host.textContent).toContain("Pinterest login required.");
  expect(host.textContent).toContain("START_SCOUT_TIKTOKSHOP.cmd");
  expect(host.textContent).toContain("do not create a new pairing");
  await act(async () => root.unmount());
});
