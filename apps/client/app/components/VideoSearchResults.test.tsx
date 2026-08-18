import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { VideoSearchResults, formatVideoDuration, formatVideoTimestamp } from "./VideoSearchResults";
import type { VideoSearchItem } from "../hooks/useVideoSearch";
const item: VideoSearchItem = { source_asset_id: "asset-a", analysis_run_id: "run-a", filename: "ride.mp4", mime_type: "video/mp4", duration_ms: 30000, source_type: "google_drive", external_source_id: "source-a", external_asset_id: "file-a", web_url: null, thumbnail_url: null, score: 10.5, best_match: { start_ms: 12000, end_ms: 18500, summary: "horse riding through field", visual_description: "rider on a horse", speech: "", confidence: 0.92, score: 6.1 }, matches: [] };
describe("VideoSearchResults", () => { it("renders backend best match directly with deterministic time formatting", () => { const markup = renderToStaticMarkup(<VideoSearchResults items={[item]} onOpen={() => {}} />); expect(markup).toContain("ride.mp4"); expect(markup).toContain("Duration 00:30"); expect(markup).toContain("Best match - 00:12"); expect(markup).toContain("horse riding through field"); expect(markup).toContain("Video placeholder for ride.mp4");
    expect(markup).toContain("Open at 00:12"); expect(formatVideoTimestamp(3_735_000)).toBe("1:02:15"); expect(formatVideoDuration(30_000)).toBe("00:30"); }); });
