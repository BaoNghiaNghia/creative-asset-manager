/**
 * Bounded background scheduler for Stage 2 per-design API probes.
 * Interactive buttons deliberately bypass this queue.
 *
 * Queued requests are removed on AbortSignal abort, so switching pages/colors
 * never sends stale requests for designs the visitor cannot see anymore.
 */
export function createStage2RequestQueue(concurrency: number) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1) throw new Error("Stage 2 concurrency must be positive");

  let active = 0;
  const pending: Array<() => void> = [];

  const drain = () => {
    while (active < concurrency && pending.length) pending.shift()?.();
  };

  function schedule<T>(work: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) return Promise.reject(new DOMException("Cancelled", "AbortError"));
    return new Promise<T>((resolve, reject) => {
      let running = false;
      let settled = false;
      const cleanup = () => signal?.removeEventListener("abort", onAbort);
      const finish = (error: unknown, value?: T) => {
        if (settled) return;
        settled = true;
        cleanup();
        if (running) active--;
        if (error !== undefined) reject(error);
        else resolve(value as T);
        drain();
      };
      const task = () => {
        if (settled) return;
        running = true;
        active++;
        // An aborted active fetch must settle through its own promise. The
        // queue does not release a slot before the transport has completed.
        Promise.resolve().then(work).then(
          value => finish(undefined, value),
          error => finish(error),
        );
      };
      const onAbort = () => {
        if (running) return;
        const index = pending.indexOf(task);
        if (index >= 0) pending.splice(index, 1);
        finish(new DOMException("Cancelled", "AbortError"));
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      pending.push(task);
      drain();
    });
  }

  return { schedule };
}

/** Stage 2 row previews share three total background API slots. */
export const stage2BackgroundRequests = createStage2RequestQueue(3);
