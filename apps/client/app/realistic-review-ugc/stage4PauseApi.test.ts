import { afterEach, describe, expect, it, vi } from "vitest";
import { getStage4QueueControl, setStage4QueuePaused } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("Stage 4 durable pause API", () => {
  it("loads status and submits a boolean pause request with credentials", async () => {
    const payload = { paused: true, queued: 12, running: 2 };
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => payload,
    });
    vi.stubGlobal("fetch", fetchSpy);
    expect(await getStage4QueueControl()).toEqual(payload);
    expect(await setStage4QueuePaused(true)).toEqual(payload);
    const [statusUrl, statusInit] = fetchSpy.mock.calls[0];
    expect(statusUrl).toBe("/api/v1/realistic-review-ugc/stage4/queue-control");
    expect(statusInit.credentials).toBe("include");
    const [updateUrl, updateInit] = fetchSpy.mock.calls[1];
    expect(updateUrl).toBe(statusUrl);
    expect(updateInit.method).toBe("POST");
    expect(JSON.parse(updateInit.body)).toEqual({ paused: true });
    expect(updateInit.credentials).toBe("include");
  });
});
