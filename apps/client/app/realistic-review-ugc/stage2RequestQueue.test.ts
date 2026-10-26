import { describe, expect, it } from "vitest";
import { createStage2RequestQueue } from "./stage2RequestQueue";

describe("Stage 2 bounded API scheduler", () => {
  it("never has more than three operations active and completes every visible request", async () => {
    const queue = createStage2RequestQueue(3);
    let active = 0;
    let peak = 0;
    const resolve: Array<() => void> = [];
    const calls = Array.from({ length: 12 }, (_, index) => queue.schedule(
      () => new Promise<number>(done => {
        active++;
        peak = Math.max(peak, active);
        resolve.push(() => { active--; done(index); });
      }),
    ));
    await Promise.resolve();
    expect(peak).toBe(3);
    for (let index = 0; index < 12; index++) {
      resolve[index]();
      await new Promise(r => setTimeout(r, 0));
    }
    expect(await Promise.all(calls)).toEqual(Array.from({ length: 12 }, (_, index) => index));
    expect(peak).toBe(3);
    expect(active).toBe(0);
  });

  it("discards aborted offscreen requests before they create network traffic", async () => {
    const queue = createStage2RequestQueue(1);
    let finishFirst: (() => void) | undefined;
    const first = queue.schedule(() => new Promise<void>(resolve => { finishFirst = resolve; }));
    await Promise.resolve();
    const controller = new AbortController();
    let called = 0;
    const hidden = queue.schedule(async () => { called++; }, controller.signal);
    controller.abort();
    await expect(hidden).rejects.toMatchObject({ name: "AbortError" });
    finishFirst?.();
    await first;
    await Promise.resolve();
    expect(called).toBe(0);
  });
});
