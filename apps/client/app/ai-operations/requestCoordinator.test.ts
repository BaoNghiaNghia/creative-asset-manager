import { describe, expect, it, vi } from "vitest";
import {
  AUTO_REFRESH_SECONDS,
  DashboardRequestCoordinator,
  autoRefreshFromSearch,
  shouldAutoRefresh,
} from "./requestCoordinator";

describe("AI Operations bounded refresh coordination", () => {
  it("supports only Off, 15s, 30s and 60s and restores the URL choice", () => {
    expect(AUTO_REFRESH_SECONDS).toEqual([0, 15, 30, 60]);
    expect(autoRefreshFromSearch("?refresh=30")).toBe(30);
    expect(autoRefreshFromSearch("?refresh=12")).toBe(0);
  });

  it("pauses refresh while the document is hidden", () => {
    expect(shouldAutoRefresh(30, "visible")).toBe(true);
    expect(shouldAutoRefresh(30, "hidden")).toBe(false);
    expect(shouldAutoRefresh(0, "visible")).toBe(false);
  });

  it("aborts an earlier request and ignores its late result", async () => {
    const coordinator = new DashboardRequestCoordinator();
    let firstSignal: AbortSignal | undefined;
    let finishFirst!: (value: string) => void;
    const first = coordinator.run(signal => {
      firstSignal = signal;
      return new Promise<string>(resolve => { finishFirst = resolve; });
    });
    const second = coordinator.run(async signal => {
      expect(signal.aborted).toBe(false);
      return "new";
    });
    expect(firstSignal?.aborted).toBe(true);
    finishFirst("stale");
    expect(await first).toEqual({ current: false });
    expect(await second).toEqual({ current: true, value: "new" });
  });

  it("aborts the active request during component-style cleanup", async () => {
    const coordinator = new DashboardRequestCoordinator();
    let signal: AbortSignal | undefined;
    let finish!: () => void;
    const pending = coordinator.run(current => {
      signal = current;
      return new Promise<void>(resolve => { finish = resolve; });
    });
    coordinator.abort();
    expect(signal?.aborted).toBe(true);
    finish();
    expect(await pending).toEqual({ current: false });
  });

  it("keeps at most one active request even across repeated refresh triggers", async () => {
    const coordinator = new DashboardRequestCoordinator();
    const signals: AbortSignal[] = [];
    const resolvers: Array<() => void> = [];
    const request = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<void>(resolve => resolvers.push(resolve));
    });
    const first = coordinator.run(request);
    const second = coordinator.run(request);
    const third = coordinator.run(request);
    expect(signals.map(item => item.aborted)).toEqual([true, true, false]);
    resolvers.forEach(resolve => resolve());
    expect(await first).toEqual({ current: false });
    expect(await second).toEqual({ current: false });
    expect(await third).toEqual({ current: true, value: undefined });
  });
});
