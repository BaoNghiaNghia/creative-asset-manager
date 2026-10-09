// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RrugcWorkflowSettingsModal } from "./RrugcWorkflowSettingsModal";
import { scoutMachineStatus } from "./PinterestAutoScoutPanel";
import type { ScoutAgent } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./api", () => ({
  getRrugcHealth: vi.fn(async () => ({
    orphan_analysis_queued: 0, stale_importing: 0, gemini_deferred: 0,
    gemini_capacity_available: true, oldest_analysis_queue_age_seconds: 7200,
    scout_total: 2, scout_online: 1, scout_offline: 1, scout_outdated: 0,
    checked_at: "2026-10-09T05:00:00Z",
  })),
  listScoutAgents: vi.fn(async () => [
    { id: "keyword-1", name: "Keyword Worker", machine_label: "BaoNghia", status: "ready", client_version: "rrugc-scout-v54", active: true, last_seen_at: "2026-10-09T05:00:00Z" },
    { id: "review-1", name: "Review Worker", machine_label: "DESKTOP-91TD9B5", status: "offline", client_version: "rrugc-scout-v54", active: true, last_seen_at: "2026-10-09T02:00:00Z" },
  ]),
  listScoutRuns: vi.fn(async () => []),
  listScoutMetrics: vi.fn(async () => ({
    overview: { total_keywords: 1787, added_24h: 194, added_7d: 1787, priority_pending: 79 },
    feedback: { suggested: 80, blocked: 132 },
    items: [{
      mode: "keyword", agent_id: "keyword-1", machine_label: "BaoNghia",
      scanned_pins: 55, found_quotes: 21, new_keywords: 26, duplicate_pins: 5,
      errors: 0, last_activity_at: "2026-10-09T05:00:00Z",
    }],
    review_items: [{
      agent_id: "review-1", submitted: 4473, new_references: 4265,
      duplicates: 208, runs: 168, failed_runs: 62, last_activity_at: "2026-10-09T02:00:00Z",
    }],
  })),
  createScoutAgent: vi.fn(), resetScoutAgentPairing: vi.fn(),
}));

afterEach(() => { document.body.replaceChildren(); });

describe("Scout settings status-led layout", () => {
  it("distinguishes machine health states from actual agent status", () => {
    const agent = (status: ScoutAgent["status"]) => ({ status } as ScoutAgent);
    expect(scoutMachineStatus(agent("ready")).label).toBe("Online");
    expect(scoutMachineStatus(agent("busy")).tone).toBe("online");
    expect(scoutMachineStatus(agent("offline")).icon).toBe("wifi-off");
    expect(scoutMachineStatus(agent("error")).label).toBe("Error");
    expect(scoutMachineStatus(agent("needs_login")).tone).toBe("warning");
    expect(scoutMachineStatus(undefined).label).toBe("Unlinked");
  });

  it("renders distinct Keyword/Review cards, icons, and health warnings", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<RrugcWorkflowSettingsModal open onClose={() => undefined} onError={() => undefined} />);
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    expect(host.querySelectorAll(".rrugc-scout-machine-section")).toHaveLength(2);
    expect(host.querySelector('[aria-label="Keyword Scout machines"]')?.textContent).toContain("BaoNghia");
    expect(host.querySelector('[aria-label="Review Scout machines"]')?.textContent).toContain("DESKTOP-91TD9B5");
    expect(host.querySelector(".rrugc-scout-machine-status.is-online")?.textContent).toBe("Online");
    expect(host.querySelector(".rrugc-scout-machine-status.is-offline")?.textContent).toBe("Offline");
    expect(host.querySelector(".rrugc-health-grid article.is-warning")).not.toBeNull();
    for (const icon of ["heartbeat", "monitor", "key", "image", "wifi-off", "clock"]) {
      expect(host.querySelector(`[data-workflow-icon="${icon}"]`)).not.toBeNull();
    }
    await act(async () => root.unmount());
  });
});
