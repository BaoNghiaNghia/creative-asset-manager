import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  fetchVideoCdnDeliveryRuntimeStatus,
  updateVideoCdnDeliveryRuntime,
  type VideoCdnDeliveryRuntimeStatus,
} from "../../features/settings";
import { VideoCdnDeliverySettings, VideoCdnDeliverySettingsView } from "./VideoCdnDeliverySettings";

const status: VideoCdnDeliveryRuntimeStatus = {
  setting: "VIDEO_CDN_DELIVERY_ENABLED",
  runtime_enabled: false,
  effective_enabled: false,
  can_enable: true,
  prerequisites: {
    r2_video_cache_enabled: true,
    delivery_configured: true,
  },
  blockers: ["runtime_toggle_disabled"],
  updated_at: null,
};

describe("Video CDN delivery Phase 4A settings", () => {
  it("renders an accessible platform runtime toggle without exposing delivery secrets", () => {
    const markup = renderToStaticMarkup(<VideoCdnDeliverySettingsView
      status={status}
      reason="approved rollout"
      busy={false}
      error=""
      notice=""
      onReasonChange={() => undefined}
      onToggle={() => undefined}
    />);
    for (const value of [
      "Video CDN delivery",
      "Runtime toggle",
      "R2 video cache",
      "Signed delivery",
      "Effective delivery",
      "Phase 4A only stores runtime intent",
      "Enable CDN delivery",
    ]) expect(markup).toContain(value);
    expect(markup).toContain('aria-pressed="false"');
    expect(markup).not.toContain("R2_VIDEO_MEDIA_SIGNING_SECRET");
    expect(markup).not.toContain("media.example");
  });

  it("blocks enable in the UI when prerequisites are unavailable but still describes the state", () => {
    const blocked = {
      ...status,
      can_enable: false,
      prerequisites: {
        r2_video_cache_enabled: false,
        delivery_configured: false,
      },
    };
    const markup = renderToStaticMarkup(<VideoCdnDeliverySettingsView
      status={blocked}
      reason="approved rollout"
      busy={false}
      error=""
      notice=""
      onReasonChange={() => undefined}
      onToggle={() => undefined}
    />);
    expect(markup).toContain("Server prerequisites are not ready");
    expect(markup).toMatch(/<button[^>]*disabled=/);
  });

  it("has an accessible loading state before the admin status request resolves", () => {
    const markup = renderToStaticMarkup(<VideoCdnDeliverySettings />);
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("Loading video CDN delivery settings");
  });

  it("loads and updates only the platform runtime endpoint", async () => {
    const fetcher = vi.fn(async (_url: string | URL | Request, init?: RequestInit) =>
      new Response(JSON.stringify({
        ...status,
        runtime_enabled: init?.method === "PUT",
        effective_enabled: init?.method === "PUT",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    ) as unknown as typeof fetch;

    await expect(fetchVideoCdnDeliveryRuntimeStatus(fetcher)).resolves.toMatchObject({
      runtime_enabled: false,
    });
    await expect(updateVideoCdnDeliveryRuntime(
      true,
      "approved rollout",
      fetcher,
    )).resolves.toMatchObject({ runtime_enabled: true });

    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      "/api/v1/admin/video-delivery/runtime",
      expect.objectContaining({ method: "GET", credentials: "same-origin" }),
    );
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/api/v1/admin/video-delivery/runtime",
      expect.objectContaining({
        method: "PUT",
        credentials: "same-origin",
        body: JSON.stringify({ enabled: true, reason: "approved rollout" }),
      }),
    );
  });

  it("surfaces safe API errors to the UI caller", async () => {
    const fetcher = vi.fn(async () => new Response(
      JSON.stringify({
        detail: {
          code: "video_delivery_prerequisites_unavailable",
          message: "Video CDN delivery cannot be enabled until all server prerequisites are ready.",
        },
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    )) as unknown as typeof fetch;
    await expect(updateVideoCdnDeliveryRuntime(
      true,
      "approved rollout",
      fetcher,
    )).rejects.toThrow("prerequisites are ready");
  });
});
