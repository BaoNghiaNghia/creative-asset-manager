// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { KeywordDetailModal, nearestTrendPointIndex } from "./KeywordAnalysisTable";
import type { KeywordVolume } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const keyword: KeywordVolume = {
  id: "keyword-1", keyword: "Custom Embroidery", search_volume: 22200,
  competition: "HIGH", cpc_low: 1.17, cpc_high: 5.16,
  provider: "aebrowse_google_ads", provider_account: null, fetched_at: "2026-10-10T02:00:00Z",
  source_image_url: "https://example.com/hat.jpg", source_pin_url: "https://pinterest.com/pin/123",
  picked: true, picked_at: null, favorite: false, favorite_at: null,
  trend: [
    { period: "2026-01", volume: 18000 },
    { period: "2026-02", volume: 20000 },
    { period: "2026-03", volume: 33000 },
    { period: "2026-04", volume: 22000 },
  ],
};

afterEach(() => { document.body.replaceChildren(); });

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
