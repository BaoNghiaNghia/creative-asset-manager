import { describe, expect, it, vi } from "vitest";
import { routeForPath } from "../AppRoute";
import { createGeneration, videoResultUrl } from "./api";

describe("DG-06 video generation contracts", () => {
  it("routes base and direct generation URLs without changing existing routes", () => {
    expect(routeForPath("/video-generation")).toBe("video-generation");
    expect(routeForPath("/video-generation/run-123")).toBe("video-generation");
    expect(routeForPath("/job-queue")).toBe("job-queue");
    expect(routeForPath("/")).toBe("explorer");
  });
  it("posts only to CAM and preserves supplied idempotency request values", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "g1", status: "queued" }), { status: 202, headers: { "Content-Type": "application/json" } }));
    const request = { client_request_id: "stable-id", prompt: "A studio product video", model: "seedance-2.5", aspect_ratio: "16:9", duration_seconds: 10, reference_asset_ids: ["asset-b", "asset-a"] };
    await createGeneration(request, undefined, fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/video-generations", expect.objectContaining({ method: "POST", credentials: "same-origin" }));
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(request);
    expect(videoResultUrl("g1")).toBe("/api/v1/video-generations/g1/video");
  });
  it("uses only the capability-driven CAM endpoints", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ enabled: true, models: ["custom"], aspect_ratios: ["1:1"], durations: [10], max_references: 8, allowed_reference_mime_types: ["image/png"] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const { fetchCapabilities } = await import("./api");
    const capabilities = await fetchCapabilities(undefined, fetcher);
    expect(capabilities.models).toEqual(["custom"]);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/video-generations/capabilities", expect.objectContaining({ credentials: "same-origin" }));
  });
});
