import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  fetchVideoCdnDeliveryObservability,
  fetchVideoCdnDeliveryRuntimeStatus,
  updateVideoCdnDeliveryRuntime,
  type VideoCdnDeliveryObservability,
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
    rollout_scope_configured: true,
    delivery_guard_enabled: true,
  },
  blockers: ["runtime_toggle_disabled"],
  rollout_mode: "canary",
  canary_tenant_count: 1,
  updated_at: null,
};

const observability: VideoCdnDeliveryObservability = {
  metrics: {
    scope: "process_local",
    counters: {
      video_cdn_redirect_total: 12,
      video_cdn_fallback_guard_total: 2,
      video_cdn_fallback_cache_miss_total: 1,
      video_cdn_probe_failure_total: 1,
    },
    decision_latency_ms: {
      sample_count: 15,
      p50: 8.1,
      p95: 21.4,
      max: 34.2,
    },
  },
  guard: {
    scope: "process_local",
    enabled: true,
    state: "closed",
    consecutive_failures: 0,
    failure_threshold: 3,
    probe_interval_seconds: 30,
    cooldown_seconds: 120,
    open_remaining_seconds: 0,
  },
};

describe("Video CDN Phase 5B activation console", () => {
  it("renders rollout prerequisites and identity-free observability", () => {
    const markup = renderToStaticMarkup(<VideoCdnDeliverySettingsView
      status={status}
      observability={observability}
      reason="approved rollout"
      busy={false}
      error=""
      notice=""
      onReasonChange={() => undefined}
      onToggle={() => undefined}
      onRefresh={() => undefined}
    />);
    for (const value of [
      "Video CDN activation",
      "DELIVERY INACTIVE",
      "Canary · 1 tenant",
      "Health guard",
      "Circuit breaker",
      "Redirects",
      "12",
      "Provider fallbacks",
      "3",
      "Decision p95",
      "21 ms",
      "Enable canary delivery",
    ]) expect(markup).toContain(value);
    expect(markup).toContain('aria-pressed="false"');
    expect(markup).not.toContain("tenant-a");
    expect(markup).not.toContain("R2_VIDEO_MEDIA_SIGNING_SECRET");
    expect(markup).not.toContain("media.example");
  });

  it("blocks activation when the delivery guard is disabled", () => {
    const blocked: VideoCdnDeliveryRuntimeStatus = {
      ...status,
      can_enable: false,
      prerequisites: {
        ...status.prerequisites,
        delivery_guard_enabled: false,
      },
      blockers: ["runtime_toggle_disabled", "video_delivery_guard_disabled"],
    };
    const markup = renderToStaticMarkup(<VideoCdnDeliverySettingsView
      status={blocked}
      observability={{ ...observability, guard: { ...observability.guard, enabled: false, state: "disabled" } }}
      reason="approved rollout"
      busy={false}
      error=""
      notice=""
      onReasonChange={() => undefined}
      onToggle={() => undefined}
      onRefresh={() => undefined}
    />);
    expect(markup).toContain("Activation is blocked");
    expect(markup).toContain("video delivery guard disabled");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*aria-pressed="false"|<button[^>]*aria-pressed="false"[^>]*disabled=""/);
  });

  it("has an accessible loading state before activation status resolves", () => {
    const markup = renderToStaticMarkup(<VideoCdnDeliverySettings />);
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain("Loading runtime, rollout, and circuit-breaker status");
  });

  it("uses only admin runtime and observability endpoints", async () => {
    const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const value = String(url);
      const payload = value.endsWith("/observability")
        ? observability
        : {
            ...status,
            runtime_enabled: init?.method === "PUT",
            effective_enabled: init?.method === "PUT",
          };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    await expect(fetchVideoCdnDeliveryRuntimeStatus(fetcher)).resolves.toMatchObject({
      rollout_mode: "canary",
    });
    await expect(fetchVideoCdnDeliveryObservability(fetcher)).resolves.toMatchObject({
      guard: { state: "closed" },
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
      "/api/v1/admin/video-delivery/observability",
      expect.objectContaining({ method: "GET", credentials: "same-origin" }),
    );
    expect(fetcher).toHaveBeenNthCalledWith(
      3,
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


describe("Video CDN activation rollback resilience", () => {
  it("keeps runtime API independent from observability API failures", async () => {
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      if (String(url).endsWith("/observability")) {
        return new Response("unavailable", { status: 503 });
      }
      return new Response(JSON.stringify(status), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    await expect(fetchVideoCdnDeliveryRuntimeStatus(fetcher)).resolves.toMatchObject({
      runtime_enabled: false,
    });
    await expect(fetchVideoCdnDeliveryObservability(fetcher)).rejects.toThrow();
  });
});
