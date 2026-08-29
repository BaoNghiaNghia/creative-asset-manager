export type AiOpsProvider = "gemini" | "openai";
export type AiOpsMode = "single" | "batch";

export type AiOpsFilters = {
  range: 0 | 30 | 90 | 180;
  provider: string;
  model: string;
  processingMode: string;
  metadataProfile: string;
  status: string;
  page: number;
  pageSize?: 25 | 50 | 100;
  usagePage?: number;
  usagePageSize?: 25 | 50 | 100;
  pipelinePage?: number;
  pipelinePageSize?: 25 | 50 | 100;
  videoPage?: number;
  videoPageSize?: 25 | 50 | 100;
};

export type AiOpsCost = {
  estimated_cost_micros: number;
  provider_reported_cost_micros: number;
  reconciled_cost_micros: number;
  currency: string;
};

export type AiOpsSummary = {
  requested: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  budget_blocked: number;
  deferred: number;
  next_deferred_retry_at: string | null;
  local_rate_limited: number;
  quota_deferred: number;
  provider_cooldown_deferred: number;
  next_local_rate_limit_retry_at: string | null;
  next_quota_retry_at: string | null;
  next_provider_retry_at?: string | null;
  success_rate: number;
  input_units: number;
  output_units: number;
  cost: AiOpsCost;
  latency: { average_ms: number; p50_ms: number; p95_ms: number };
  average_cost_per_completed_asset_micros: number;
};

export type AiOpsDaily = {
  date: string;
  requested: number;
  completed: number;
  failed: number;
  estimated_cost_micros: number;
  provider_reported_cost_micros: number;
  reconciled_cost_micros: number;
  provider_estimated_cost_micros: Record<string, number>;
  average_latency_ms: number;
  p95_latency_ms: number;
};

export type AiOpsProviderBreakdown = {
  provider: string;
  model: string | null;
  processing_mode: string;
  count: number;
  completed: number;
  failed: number;
  success_rate: number;
  average_latency_ms: number;
  p95_latency_ms: number;
  input_units: number;
  output_units: number;
  estimated_cost_micros: number;
  provider_reported_cost_micros: number;
  reconciled_cost_micros: number;
  currency: string;
};

export type AiOpsFailure = { source: string; error_code: string; count: number };

export type AiOpsJob = {
  id: string;
  job_type: string;
  entity_type: string;
  entity_id: string;
  asset_id: string | null;
  filename?: string | null;
  mime_type?: string | null;
  thumbnail_url?: string | null;
  provider: string | null;
  status: string;
  priority: number;
  attempt_count: number;
  max_attempts: number;
  processing_duration_ms: number;
  next_attempt_at: string | null;
  is_deferred: boolean;
  waiting_reason: string | null;
  claimed_at: string | null;
  lease_expires_at: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  error: { code: string; retryable: boolean } | null;
};

export type AiOpsUsage = {
  id: string;
  asset_id: string | null;
  analysis_id: string | null;
  job_id: string | null;
  provider: string;
  model: string | null;
  processing_mode: string;
  metadata_profile: string | null;
  metadata_profile_version: string | null;
  input_units: number;
  output_units: number;
  media_units: number;
  estimated_cost_micros: number | null;
  provider_reported_cost_micros: number | null;
  currency: string;
  latency_ms: number;
  outcome: string;
  retry_count: number;
  occurred_at: string;
};

export type Page<T> = { page: number; page_size: number; total: number; items: T[] };

export type AiOpsMediaStage = {
  key: string; label: string; queued: number; eligible_now: number; running: number;
  completed: number; failed: number; waiting_rate_limit: number; state?: "idle" | "running" | "waiting_rate_limit";
};
export type AiOpsWorkerStatus = {
  role: "image" | "video"; live: boolean | null; ready: boolean | null;
  probe: "available" | "unavailable"; active_jobs: number;
  current_job_type: string | null; last_successful_claim_at: string | null;
};
export type AiOpsVideoAnalytics = {
  daily: AiOpsDaily[];
  providers: AiOpsProviderBreakdown[];
  failures: AiOpsFailure[];
  latency: { average_ms: number; p95_ms: number };
  cost_available: boolean;
};

export type AiOpsVideoMatch = {
  start_ms: number; end_ms: number; summary: string; visual_description: string;
  speech: string; confidence: number; score: number;
};
export type AiOpsVideoDetail = {
  source_asset_id: string; analysis_run_id: string; filename: string; mime_type: string;
  duration_ms: number | null; source_type: string | null; external_source_id: string | null;
  external_asset_id: string | null; web_url: string | null; thumbnail_url: string | null;
  location: string | null; size_bytes: number | null; modified_at: string | null;
  score: number; best_match: AiOpsVideoMatch; matches: AiOpsVideoMatch[];
};
export type AiOpsRecentVideo = {
  job_id: string; source_asset_id: string; asset_id: string | null; filename: string | null;
  mime_type?: string | null; location: string | null; thumbnail_url: string | null;
  duration_ms: number | null; completed_chunks?: number; total_chunks?: number; status: string;
  attempt_count: number; max_attempts: number; updated_at: string; error_code: string | null;
  steps?: AiOpsRecentVideoStep[];
};
export type AiOpsRecentVideoStep = {
  key: string; label: string; status: string; attempt_count: number; max_attempts: number;
  updated_at: string | null; error_code: string | null;
};

export type AiOpsMediaDashboard = {
  image: AiOpsMediaStage; video: AiOpsMediaStage; video_indexing: AiOpsMediaStage;
  video_processed_today: number;
  pipeline: { image: AiOpsMediaStage[]; video: AiOpsMediaStage[] };
  analytics: AiOpsVideoAnalytics;
  recent_video: Page<AiOpsRecentVideo>;
  workers: AiOpsWorkerStatus[]; generated_at: string;
};

export type AiOpsDashboardData = {
  summary: AiOpsSummary | null;
  today: AiOpsSummary | null;
  month: AiOpsSummary | null;
  daily: AiOpsDaily[];
  providers: AiOpsProviderBreakdown[];
  todayProviders: AiOpsProviderBreakdown[];
  failures: AiOpsFailure[];
  jobs: Page<AiOpsJob>;
  usage: Page<AiOpsUsage>;
  coverage?: AiOpsSearchCoverage | null;
  pipeline?: PipelineSnapshot | null;
  media?: AiOpsMediaDashboard | null;
};

export type AiOpsAudit = { actor: string; action: string; reason: string; timestamp: string };
export type AiOpsProviderConfiguration = {
  id: AiOpsProvider;
  label: string;
  enabled: boolean;
  connection_configured: boolean;
  processing_enabled: boolean;
  paused: boolean;
  single_enabled: boolean;
  batch_enabled: boolean;
  default_model: string;
  allowed_models: string[];
  active_jobs_limit: number;
  single_concurrency: number;
  batch_concurrency: number;
  last_error: string | null;
};
export type AiOpsConfiguration = {
  tenant_id: string;
  scope: { tenant: string; global_upper_bounds_read_only: boolean };
  permissions: {
    can_manage_tenant: boolean;
    can_configure_provider?: boolean;
    can_read_budget?: boolean;
    can_update_budget?: boolean;
    can_emergency_stop?: boolean;
    can_retry_jobs?: boolean;
    can_cancel_jobs?: boolean;
    can_manage_global: boolean;
    platform_admin: boolean;
  };
  tenant: {
    ai_enabled: boolean;
    video_enabled?: boolean;
    processing_paused: boolean;
    default_provider: AiOpsProvider | null;
    default_model: string | null;
    default_mode: AiOpsMode;
    default_metadata_profile: string | null;
    auto_analyze_new_assets: boolean;
    daily_item_limit: number;
    total_ai_concurrency: number;
    retry_count: number;
    timeout_seconds: number;
  };
  global: { ai_auto_analyze_enabled: boolean; single_enabled: boolean; batch_enabled: boolean; emergency_stop: boolean };
  providers: AiOpsProviderConfiguration[];
  metadata_profiles: string[];
  metadata_prompt_template: {
    id: string | null;
    profile_name: string;
    profile_version: string;
    prompt_template: string;
    updated_at: string | null;
    is_draft: boolean;
  } | null;
  video_prompt_template?: {
    id: string | null;
    profile_name: string;
    profile_version: string;
    prompt_template: string;
    updated_at: string | null;
    is_draft: boolean;
  } | null;
  budget: {
    enabled: boolean;
    daily_limit_micros: number | null;
    monthly_limit_micros: number | null;
    warning_threshold_percent: number;
    hard_stop_threshold_percent: number;
    currency: string;
  } | null;
};

export type AiOpsSearchCoverage = {
  completed_analysis_assets: number;
  current_projection_assets: number;
  v3_indexed_documents: number;
  projection_missing: number;
  projection_stale: number;
  indexing_backlog: number;
  search_failed: number;
  database_indexed_document_missing: number;
  coverage_percent: number;
  last_audited_at: string | null;
  elasticsearch_verification_included: boolean;
  repair_jobs: { queued: number; running: number; completed: number; failed: number };
};

export type PipelineStage = {
  key: string; label: string; subtitle: string;
  total: number; pending: number; eligible_now: number; waiting: number; processing: number; completed: number; failed: number;
  total_logical_assets: number; completed_assets: number; queued_assets: number; eligible_now_assets: number; waiting_assets: number; processing_assets: number; needs_attention_assets: number; skipped_assets: number; not_started_assets: number;
  total_attempts: number; completed_attempts: number; failed_attempts: number;
  percentage: number | null; oldest_pending_at?: string | null;
};
export type PipelineActiveJob = {
  stage: string; job_type: string; status: string; filename: string | null; provider: string | null; attempt_count: number; max_attempts: number; started_at: string | null; elapsed_ms: number | null; message: string;
};
export type PipelineSnapshot = {
  generated_at: string;
  latest_source_sync: { mode: string; status: string; pages_count: number; items_seen_count: number; jobs_created_count: number; started_at: string; completed_at: string | null; duration_ms: number | null; error_code: string | null } | null;
  overall: { source_items_discovered: number; supported_assets: number; eligible_assets?: number; unsupported_assets: number; completed: number; search_ready_assets?: number; active: number; in_progress_assets?: number; queued: number; queued_assets?: number; failed: number; needs_attention_assets?: number; skipped: number; skipped_assets?: number; indexed_percentage: number | null; throughput_today: number; asset_progress: Array<{ key: string; count: number }>; };
  stages: PipelineStage[];
  active_job: PipelineActiveJob | null;
  failure_groups: Array<{ stage: string; error_code: string; category?: string; message: string; count: number; latest_at: string }>;
  skipped_breakdown?: Array<{ category: string; count: number }>;
  diagnostics?: { decommissioned_sources_excluded: number; raw_attempts: Record<string, { total_attempts: number; completed_attempts: number; failed_attempts: number }> };
  definitions?: { snapshot: string; attempt_diagnostics: string };
  recent_assets: Page<{ asset_id: string | null; filename: string; mime_type?: string | null; thumbnail_url?: string | null; state: string; stage_statuses: Record<string, string>; updated_at: string; error_code: string | null }>;
};
