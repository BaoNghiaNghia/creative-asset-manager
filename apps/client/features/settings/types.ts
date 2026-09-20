export type VideoCdnDeliveryRuntimeStatus = {
  setting: "VIDEO_CDN_DELIVERY_ENABLED";
  runtime_enabled: boolean;
  effective_enabled: boolean;
  can_enable: boolean;
  prerequisites: {
    r2_video_cache_enabled: boolean;
    delivery_configured: boolean;
  };
  blockers: string[];
  updated_at: string | null;
};
