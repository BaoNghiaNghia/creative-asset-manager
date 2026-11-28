// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { formatObservedAge, ObservedAge, ObservedClock } from "./ObservedAge";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("Etsy Spy observed age", () => {
  const now = Date.parse("2026-11-24T05:00:00Z");
  const ago = (minutes: number) => new Date(now - minutes * 60_000).toISOString();

  it.each([
    [0, "Vừa xong"], [0.99, "Vừa xong"], [1, "1 phút trước"],
    [59.99, "59 phút trước"], [60, "1 tiếng trước"], [360, "6 tiếng trước"],
    [1439.99, "23 tiếng trước"], [1440, "1 ngày trước"], [4320, "3 ngày trước"],
    [10079, "6 ngày trước"], [10080, "1 tuần trước"], [20160, "2 tuần trước"],
  ])("formats %s elapsed minutes as %s", (minutes, expected) => {
    expect(formatObservedAge(ago(minutes), now)).toBe(expected);
  });

  it("handles unavailable, invalid and future dates", () => {
    expect(formatObservedAge(null, now)).toBe("—");
    expect(formatObservedAge("invalid", now)).toBe("—");
    expect(formatObservedAge(ago(-0.5), now)).toBe("Vừa xong");
    expect(formatObservedAge(ago(-5), now)).toBe("Chưa đến");
  });

  it("refreshes labels with one clock while preserving original timestamp and tooltip", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      const timestamp = ago(59.5);
      await act(async () => root.render(<ObservedClock>
        <ObservedAge value={timestamp} />
        <ObservedAge value={null} />
      </ObservedClock>));
      const time = container.querySelector("time")!;
      expect(time.textContent).toBe("59 phút trước");
      expect(time.getAttribute("datetime")).toBe(timestamp);
      expect(time.title).toContain("GMT+7");
      await act(async () => vi.advanceTimersByTime(60_000));
      expect(container.querySelector("time")).toBe(time);
      expect(time.textContent).toBe("1 tiếng trước");
      expect(container.querySelector(".is-missing")?.textContent).toBe("—");
      expect(vi.getTimerCount()).toBe(1);
    } finally {
      await act(async () => root.unmount());
      vi.useRealTimers();
    }
  });
});
