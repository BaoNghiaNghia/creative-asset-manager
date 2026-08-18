import { describe, expect, it } from "vitest";
import { buildVideoPlaybackUrl, playbackProvider, playbackSeekSeconds, seekVideoAt } from "./videoPlayback";
const item = { source_type: "google_drive", external_source_id: "source a", external_asset_id: "external/file", best_match: { start_ms: 12000 } };
describe("video provider playback URL", () => {
  it("uses external_asset_id and scoped provider metadata without tenant or token", () => { const url = buildVideoPlaybackUrl(item); expect(url).toBe("/api/explorer/media/external%2Ffile?provider=google-drive&external_source_id=source+a"); expect(url).not.toContain("tenant_id"); expect(url).not.toContain("token"); });
  it("maps only supported provider types", () => { expect(playbackProvider("google_drive")).toBe("google-drive"); expect(playbackProvider("sharepoint")).toBe("sharepoint"); expect(buildVideoPlaybackUrl({ ...item, source_type: "unknown" })).toBeNull(); });
});
describe("video playback seek", () => {
  it("uses backend best-match milliseconds and clamps safely", () => { expect(playbackSeekSeconds(0, 60)).toBe(0); expect(playbackSeekSeconds(12000, 60)).toBe(12); expect(playbackSeekSeconds(65000, 60)).toBe(60); const video = { currentTime: 0, duration: 30 } as HTMLVideoElement; expect(seekVideoAt(video, 65000)).toBe(30); expect(video.currentTime).toBe(30); });
});
