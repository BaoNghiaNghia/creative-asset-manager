export type VideoCdnDeliveryRuntimeStatus = {
  setting: "VIDEO_CDN_DELIVERY_ENABLED";
  runtime_enabled: boolean;
  effective_enabled: boolean;
  can_enable: boolean;
  prerequisites: {
    r2_video_cache_enabled: boolean;
    delivery_configured: boolean;
    rollout_scope_configured: boolean;
    delivery_guard_enabled: boolean;
  };
  blockers: string[];
  rollout_mode: "disabled" | "canary" | "global";
  canary_tenant_count: number;
  updated_at: string | null;
};

export type VideoCdnDeliveryObservability = {
  metrics: {
    scope: "process_local";
    counters: Record<string, number>;
    decision_latency_ms: {
      sample_count: number;
      p50: number | null;
      p95: number | null;
      max: number | null;
    };
  };
  guard: {
    scope: "process_local";
    enabled: boolean;
    state: "disabled" | "open" | "probing" | "degraded" | "closed" | "unverified";
    consecutive_failures: number;
    failure_threshold: number;
    probe_interval_seconds: number;
    cooldown_seconds: number;
    open_remaining_seconds: number;
  };
};
