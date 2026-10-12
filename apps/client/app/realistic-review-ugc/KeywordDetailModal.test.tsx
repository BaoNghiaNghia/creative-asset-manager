// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { KeywordAnalysisTable, KeywordDetailModal, nearestTrendPointIndex } from "./KeywordAnalysisTable";
import type { KeywordVolume } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const keyword: KeywordVolume = {
  id: "keyword-1", keyword: "Custom Embroidery", search_volume: 22200,
  competition: "HIGH", cpc_low: 1.17, cpc_high: 5.16,
  provider: "aebrowse_google_ads", provider_account: null, fetched_at: "2026-10-10T02:00:00Z",
  source_image_url: "https://example.com/hat.jpg", source_pin_url: "https://pinterest.com/pin/123",
  source_agents: [{ agent_key: "scout:reach", agent_name: "Agent-Reach Auto Discover", platforms: ["etsy"], evidence_count: 1 }],
  picked: true, picked_at: null, favorite: false, favorite_at: null,
  trend: [
    { period: "2026-01", volume: 18000 },
    { period: "2026-02", volume: 20000 },
    { period: "2026-03", volume: 33000 },
    { period: "2026-04", volume: 22000 },
  ],
};

afterEach(() => { document.body.replaceChildren(); });

describe("Stage 0 platform source attribution", () => {
  it("shows a Pinterest icon and text for Pin-sourced keywords, without inventing a platform for missing sources", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const noop = () => undefined;
    const data = {
      items: [keyword, { ...keyword, id: "keyword-no-pin", keyword: "Manual Word", source_pin_url: null, source_agents: [] }],
      page: 1, page_size: 20, total: 2,
      overview: {
        total_keywords: 2, total_search_volume: 22200, high_competition: 0, zero_volume: 0,
        short_tail_keywords: 2, mid_tail_keywords: 0, long_tail_keywords: 0,
        picked_keywords: 1, favorite_keywords: 0, suggested_keywords: 0,
      },
    };
    await act(async () => root.render(<KeywordAnalysisTable
      data={data} query="" sortBy="keyword" sortDirection="asc"
      usageFilter="all" tailFilter="all" favoritesOnly={false} suggestedOnly={false}
      pickingIds={new Set()} favoritingIds={new Set()} feedbackUpdatingIds={new Set()}
      onFeedbackChange={noop} onSortChange={noop} onUsageFilterChange={noop}
      onTailFilterChange={noop} onFavoritesOnlyChange={noop}
      onSuggestedOnlyChange={noop} onResetAll={noop}
      onPickChange={noop} onFavoriteChange={noop}
      onPageChange={noop} onPageSizeChange={noop} onQueryChange={noop}
    />));
    const headers = Array.from(host.querySelectorAll(".rrugc-stage0-keyword-table th")).map(header => header.textContent);
    expect(headers.slice(0, 3)).toEqual(["Preview", "Keyword↑", "Trend"]);
    expect(headers.indexOf("Agent")).toBe(headers.indexOf("Platform") + 1);
    expect(host.querySelector(".rrugc-stage0-agent-badge")?.getAttribute("aria-label")).toContain("Agent-Reach Auto Discover");
    expect(host.querySelector(".rrugc-stage0-agent-empty")?.textContent).toBe("—");
    expect(host.querySelectorAll(".rrugc-stage0-keyword-table tbody tr")).toHaveLength(2);
    expect(host.querySelector(".rrugc-stage0-platform")?.textContent).toBe("Pinterest");
    expect(host.querySelector(".rrugc-stage0-platform svg")).not.toBeNull();
    expect(host.querySelector(".rrugc-stage0-platform-empty")?.textContent).toBe("—");
    await act(async () => root.unmount());
  });
});

describe("Keyword detail trend chart", () => {
  it("selects the nearest month across the full chart width, including the empty spaces between dots", () => {
    expect(nearestTrendPointIndex(100, 100, 720, 4)).toBe(0);
    expect(nearestTrendPointIndex(100 + 48, 100, 720, 4)).toBe(0);
    expect(nearestTrendPointIndex(100 + 380, 100, 720, 4)).toBe(2);
    expect(nearestTrendPointIndex(100 + 706, 100, 720, 4)).toBe(3);
    expect(nearestTrendPointIndex(2000, 100, 720, 4)).toBe(3);
    expect(nearestTrendPointIndex(100, 100, 0, 4)).toBe(-1);
  });

  it("keeps tooltip responsive on consecutive hovers, restores it after leaving and supports keyboard", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => root.render(<KeywordDetailModal item={keyword} onClose={() => undefined} />));
    const chart = host.querySelector<SVGSVGElement>(".rrugc-stage0-detail-chart-svg")!;
    expect(chart).not.toBeNull();
    Object.defineProperty(chart, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ left: 100, width: 720, right: 820, top: 0, height: 250, bottom: 250 }),
    });
    const move = async (x: number) => act(async () => chart.dispatchEvent(
      new MouseEvent("pointermove", { bubbles: true, clientX: x }),
    ));
    await move(148);
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("Jan 2026");
    // This coordinate is deliberately between dots, where the old 13px hit circles lost hover.
    await move(480);
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("Mar 2026");
    await move(805);
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("Apr 2026");
    await act(async () => chart.dispatchEvent(new MouseEvent("pointerout", { bubbles: true, relatedTarget: null })));
    expect(host.querySelector('[role="tooltip"]')).toBeNull();
    await move(260);
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("Feb 2026");
    // Polling may create a new record object and closure; hover must not reset.
    await act(async () => root.render(<KeywordDetailModal item={{ ...keyword }} onClose={() => undefined} />));
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("Feb 2026");
    await act(async () => chart.dispatchEvent(new KeyboardEvent("keydown", {
      bubbles: true, key: "ArrowRight",
    })));
    expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("Mar 2026");
    expect(host.querySelector(".rrugc-stage0-detail-guide")).not.toBeNull();
    expect(host.querySelector(".rrugc-stage0-detail-source img")).not.toBeNull();
    await act(async () => root.unmount());
  });
});
