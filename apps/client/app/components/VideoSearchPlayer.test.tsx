import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { VideoSearchPlayer } from "./VideoSearchPlayer";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
const item: VideoSearchItem = { source_asset_id: "internal-asset", analysis_run_id: "run-a", filename: "ride.mp4", mime_type: "video/mp4", duration_ms: 30000, source_type: "google_drive", external_source_id: "source-a", external_asset_id: "external-a", web_url: "https://drive.example/not-media", thumbnail_url: null, score: 9, best_match: { start_ms: 12000, end_ms: 18000, summary: "horse", visual_description: "", speech: "", confidence: .9, score: 5 }, matches: [] };
describe("VideoSearchPlayer", () => {
  it("uses the existing scoped Explorer media stream rather than provider web URL", () => { const markup = renderToStaticMarkup(<VideoSearchPlayer item={item} onClose={() => {}} />); expect(markup).toContain("/api/explorer/media/external-a?provider=google-drive&amp;external_source_id=source-a"); expect(markup).not.toContain("https://drive.example/not-media"); expect(markup).toContain("controls"); });
  it("shows a safe unavailable state for missing playback metadata", () => { const markup = renderToStaticMarkup(<VideoSearchPlayer item={{ ...item, external_asset_id: null }} onClose={() => {}} />); expect(markup).toContain("Playback unavailable for this indexed video."); });
});
