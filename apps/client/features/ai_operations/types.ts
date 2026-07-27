export type AiOpsProvider = "gemini" | "openai";
export type AiOpsMode = "single" | "batch";

export type AiOpsFilters = {
  range: 7 | 30 | 90;
  provider: string;
  model: string;
  processingMode: string;
  metadataProfile: string;
  status: string;
  page: number;
  pageSize?: 25 | 50 | 100;
  usagePage?: number;
  usagePageSize?: 25 | 50 | 100;
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