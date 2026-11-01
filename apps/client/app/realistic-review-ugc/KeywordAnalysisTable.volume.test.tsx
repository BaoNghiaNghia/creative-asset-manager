// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeywordAnalysisTable } from "./KeywordAnalysisTable";
import type { KeywordVolumePage } from "./types";
import type { KeywordVolumeFilter } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const data: KeywordVolumePage = {
  items: [], page: 1, page_size: 20, total: 2,
  overview: {
    total_keywords: 5, zero_volume: 3, total_search_volume: 1750,
    average_search_volume: 350, average_cpc: null, high_competition: 0,
    short_tail_keywords: 3, mid_tail_keywords: 2, long_tail_keywords: 0,
    picked_keywords: 2, suggested_keywords: 0,
  },
};

afterEach(() => { document.body.replaceChildren(); });

describe("Stage 0 volume KPI selection", () => {
  it("starts on positive-volume total, switches to zero, and restores positive-volume total", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onVolumeFilterChange = vi.fn();
    const onTailFilterChange = vi.fn();
    const onUsageFilterChange = vi.fn();
    const onSuggestedOnlyChange = vi.fn();
    const render = (volumeFilter: KeywordVolumeFilter) => <KeywordAnalysisTable
      data={data}
      query=""
      sortBy="search_volume"
      sortDirection="desc"
      volumeFilter={volumeFilter}
      usageFilter="all"
      tailFilter="all"
      suggestedOnly={false}
      pickingIds={new Set()}
      feedbackUpdatingIds={new Set()}
      onFeedbackChange={() => undefined}
      onSortChange={() => undefined}
      onUsageFilterChange={onUsageFilterChange}
      onTailFilterChange={onTailFilterChange}
      onVolumeFilterChange={onVolumeFilterChange}
      onSuggestedOnlyChange={onSuggestedOnlyChange}
      onResetAll={() => undefined}
      onPickChange={() => undefined}
      onPageChange={() => undefined}
      onPageSizeChange={() => undefined}
      onQueryChange={() => undefined}
    />;
    await act(async () => root.render(render("nonzero")));
    const total = host.querySelector<HTMLButtonElement>(".rrugc-stage0-total-kpi");
    const zero = host.querySelector<HTMLButtonElement>(".rrugc-stage0-zero-kpi");
    expect(total?.getAttribute("aria-pressed")).toBe("true");
    expect(total?.querySelector("strong")?.textContent).toBe("2");
    expect(zero?.getAttribute("aria-pressed")).toBe("false");
    expect(zero?.querySelector("strong")?.textContent).toBe("3");
    await act(async () => zero?.click());
    expect(onVolumeFilterChange).toHaveBeenCalledWith("zero");

    await act(async () => root.render(render("zero")));
    expect(host.querySelector(".rrugc-stage0-zero-kpi.active")).not.toBeNull();
    expect(host.querySelector(".rrugc-stage0-total-kpi.active")).toBeNull();
    await act(async () => total?.click());
    expect(onVolumeFilterChange).toHaveBeenLastCalledWith("nonzero");

    await act(async () => {
      host.querySelector<HTMLButtonElement>(".rrugc-stage0-tail-kpi-short")?.click();
    });
    expect(onVolumeFilterChange).toHaveBeenLastCalledWith("all");
    expect(onTailFilterChange).toHaveBeenCalledWith("short");
    await act(async () => root.unmount());
  });
});
