import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildVideoPlaybackTicketUrl,
  buildVideoPlaybackUrl,
  getExplorerPlaybackTicket,
  playbackProvider,
  playbackSeekSeconds,
  seekVideoAt,
} from "./videoPlayback";

const item = {
  source_type: "google_drive",
  external_source_id: "source a",
  external_asset_id: "external/file",
  best_match: { start_ms: 12000 },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("video provider playback URL", () => {
  it("uses external_asset_id and scoped provider metadata without tenant or token", () => {
    const url = buildVideoPlaybackUrl(item);
    expect(url).toBe("/api/explorer/media/external%2Ffile?provider=google-drive&external_source_id=source+a");
    expect(url).not.toContain("tenant_id");
    expect(url).not.toContain("token");
  });

  it("uses the same scoped identity for the playback ticket endpoint", () => {
    expect(buildVideoPlaybackTicketUrl(item)).toBe(
      "/api/explorer/media/external%2Ffile/playback-ticket?provider=google-drive&external_source_id=source+a",
    );
  });

  it("maps only supported provider types", () => {
    expect(playbackProvider("google_drive")).toBe("google-drive");
    expect(playbackProvider("onedrive")).toBe("onedrive");
    expect(playbackProvider("sharepoint")).toBe("sharepoint");
    expect(buildVideoPlaybackUrl({ ...item, source_type: "unknown" })).toBeNull();
  });
});

describe("Explorer playback ticket cache", () => {
  it("deduplicates inflight requests and reuses a live CDN ticket", async () => {
    const uniqueItem = { ...item, external_asset_id: "ticket-cache-test" };
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        url: "https://media.example.test/video-cache/test",
        cdn: true,
        expires_at: Math.floor(Date.now() / 1000) + 120,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([
      getExplorerPlaybackTicket(uniqueItem),
      getExplorerPlaybackTicket(uniqueItem),
    ]);

    expect(first).toEqual(second);
    expect(first.cdn).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await getExplorerPlaybackTicket(uniqueItem);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("video playback seek", () => {
  it("uses backend best-match milliseconds and clamps safely", () => {
    expect(playbackSeekSeconds(0, 60)).toBe(0);
    expect(playbackSeekSeconds(12000, 60)).toBe(12);
    expect(playbackSeekSeconds(65000, 60)).toBe(60);
    const video = { currentTime: 0, duration: 30 } as HTMLVideoElement;
    expect(seekVideoAt(video, 65000)).toBe(30);
    expect(video.currentTime).toBe(30);
  });
});
