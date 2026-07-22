export type CoordinatedResult<T> =
  | { current: true; value: T }
  | { current: true; error: unknown }
  | { current: false };

/** Keeps dashboard refreshes bounded to one active HTTP request. */
export class DashboardRequestCoordinator {
  private active: AbortController | null = null;
  private generation = 0;

  async run<T>(request: (signal: AbortSignal) => Promise<T>): Promise<CoordinatedResult<T>> {
    this.abort();
    const controller = new AbortController();
    const generation = this.generation;
    this.active = controller;
    try {
      const value = await request(controller.signal);
      return this.active === controller && this.generation === generation
        ? { current: true, value }
        : { current: false };
    } catch (error) {
      return this.active === controller && this.generation === generation
        ? { current: true, error }
        : { current: false };
    } finally {
      if (this.active === controller) this.active = null;
    }
  }

  abort() {
    this.generation += 1;
    this.active?.abort();
    this.active = null;
  }
}

export const AUTO_REFRESH_SECONDS = [0, 15, 30, 60] as const;
export type AutoRefreshSeconds = typeof AUTO_REFRESH_SECONDS[number];

export function autoRefreshFromSearch(search: string): AutoRefreshSeconds {
  const value = Number(new URLSearchParams(search).get("refresh"));
  return AUTO_REFRESH_SECONDS.includes(value as AutoRefreshSeconds) ? value as AutoRefreshSeconds : 0;
}

export function shouldAutoRefresh(seconds: AutoRefreshSeconds, visibilityState: string): boolean {
  return seconds > 0 && visibilityState === "visible";
}
