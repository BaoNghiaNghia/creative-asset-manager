export type ProductContextProfile = {
  auto_context: boolean;
  operator_themes?: string[];
  detected_themes?: string[];
  themes: string[];
  preferred_scenes: string[];
  avoid: string[];
  notes?: string | null;
  feedback_learning?: {
    active: boolean;
    minimum_consistent_reviews?: number;
    total_reviews: number;
    good_count: number;
    wrong_count: number;
    promoted_queries?: string[];
    suppressed_queries?: string[];
    query_scores?: Array<{
      query: string;
      good: number;
      wrong: number;
      reviews: number;
      score: number;
    }>;
  };
  visual_context?: {
    status: "not_analyzed" | "ready" | "stale" | string;
    themes?: string[];
    embroidery_text?: string[];
    embroidery_identity?: string | null;
    scene_hints?: string[];
    audience_hints?: string[];
    occasion_hints?: string[];
    product_cues?: string[];
    avoid_hints?: string[];
    confidence?: number;
    summary?: string | null;
    references_analyzed?: number;
    reference_ids?: string[];
    reference_views?: string[];
    providers?: string[];
    models?: string[];
    binding_fingerprint?: string;
    analyzed_at?: string | null;
    version?: string;
  };
  search_clusters?: {
    direct?: string[];
    text_match?: string[];
    adjacent?: string[];
    generic?: string[];
  };
  source?: {
    product_name?: string | null;
    product_type?: string | null;
    has_product_snapshot?: boolean;
    visual_context_status?: string;
    visual_reference_count?: number;
  };
  version?: string;
};

export type KeywordHealth = {
  query: string;
  state: "protected" | "healthy" | "explore" | "suppressed";
  protected: boolean;
  scans: number;
  found: number;
  new: number;
  duplicate: number;
  failed_scans: number;
  approved: number;
  ref_good: number;
  ref_bad: number;
  context_good: number;
  context_wrong: number;
  approved_yield: number;
  reference_yield: number;
  duplicate_rate: number;
  failure_rate: number;
};

export type Campaign = {
  id: string;
  name: string;
  query: string;
  search_queries: string[];
  search_query_anchors: string[];
  discovery_mode: "keyword" | "product_context";
  product_context: ProductContextProfile | null;
  keyword_health: KeywordHealth[];
  target_count: number;
  max_scroll_batches: number;
  auto_import: boolean;
  auto_scout: boolean;
  scan_interval_seconds: number;
  scan_next_at: string | null;
  scan_last_started_at: string | null;
  scan_last_completed_at: string | null;
  scan_attempt_count: number;
  scan_empty_streak: number;
  scan_failure_streak: number;
  scan_last_error_code: string | null;
  active_scan_run_id: string | null;
  min_head_ratio: number;
  max_head_ratio: number;
  min_smile_score: number;
  max_head_occlusion: number;
  max_ai_risk_score: number;
  min_quality_score: number;
  min_ugc_score: number;
  min_product_fit_score: number;
  require_head_visible: boolean;
  reject_headwear: boolean;
  product_id: string | null;
  product_sku: string | null;
  product_name: string | null;
  product_source_url: string | null;
  product_brand: string | null;
  product_revision: number | null;
  product_variant_ids: string[];
  product_variants: ProductVariant[];
  product_reference_count: number;
  product_reference_views: ProductReferenceView[];
  product_bound_at: string | null;
  product_binding_stale: boolean;
  generation_ready: boolean;
  auto_complete_on_delivery: boolean;
  completion_destination_id: string | null;
  completed_at: string | null;
  status: "running" | "paused" | "completed" | "stopped";
  scout_status: "offline" | "ready" | "busy" | "needs_login" | "error";
  scout_last_seen_at: string | null;
  discovered: number;
  analysis_pending: number;
  analyzing: number;
  approved: number;
  rejected: number;
  drive_ready: number;
  failed: number;
  created_at: string;
  updated_at: string;
};

export type CampaignCreated = Campaign & { scout_token: string };

export type SourcePlanReferencePreview = {
  id: string;
  pin_url: string;
  image_url: string;
  status: CandidateStatus;
  picked: boolean;
  rejected: boolean;
  source_query: string | null;
  width: number | null;
  height: number | null;
  created_at: string;
};

export type SourcePlanGroupImage = {
  id: string;
  source_name: string;
  source_relative_path: string;
  source_preview_url: string;
  source_web_url: string | null;
  source_width: number | null;
  source_height: number | null;
  source_size_bytes: number | null;
};

export type KeywordVolumeTrendPoint = {
  period: string;
  volume: number;
};

export type KeywordVolume = {
  id: string;
  keyword: string;
  search_volume: number;
  competition: string | null;
  cpc_low: number | null;
  cpc_high: number | null;
  competition_index?: number | null;
  three_month_change_pct?: number | null;
  yoy_change_pct?: number | null;
  trend?: KeywordVolumeTrendPoint[];
  source_image_url: string | null;
  source_pin_url: string | null;
  picked: boolean;
  picked_at: string | null;
  favorite: boolean;
  favorite_at: string | null;
  provider: string;
  provider_account?: string | null;
  provider_customer_id?: string | null;
  request_count?: number;
  fetched_at: string;
  last_requested_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type KeywordVolumeOverview = {
  total_keywords: number;
  total_search_volume: number;
  average_search_volume?: number;
  average_cpc?: number | null;
  high_competition: number;
  zero_volume: number;
  short_tail_keywords: number;
  mid_tail_keywords: number;
  long_tail_keywords: number;
  picked_keywords: number;
  favorite_keywords: number;
};

export type KeywordVolumePage = {
  items: KeywordVolume[];
  page: number;
  page_size: number;
  total: number;
  overview: KeywordVolumeOverview;
};

export type SourcePlan = {
  id: string;
  root_folder_id: string;
  source_file_id: string;
  source_parent_folder_id: string | null;
  source_relative_path: string;
  source_name: string;
  source_mime_type: string;
  source_size_bytes: number | null;
  source_width: number | null;
  source_height: number | null;
  source_modified_at: string | null;
  source_web_url: string | null;
  source_preview_url: string;
  source_revision: string;
  analysis_revision: number;
  embroidery_signature: string | null;
  embroidery_group_size: number;
  source_group_images?: SourcePlanGroupImage[];
  target_count: number;
  status: "queued" | "analyzing" | "ready" | "retry" | "failed" | "missing" | string;
  visual_context: {
    status?: string;
    themes?: string[];
    embroidery_text?: string[];
    embroidery_identity?: string | null;
    scene_hints?: string[];
    audience_hints?: string[];
    occasion_hints?: string[];
    product_cues?: string[];
    avoid_hints?: string[];
    confidence?: number;
    summary?: string | null;
  } | null;
  reference_contexts?: string[];
  campaign_id: string | null;
  campaign_name: string | null;
  campaign_status: Campaign["status"] | null;
  scout_status: Campaign["scout_status"] | null;
  auto_scout: boolean;
  search_queries: string[];
  progress_count: number;
  pipeline_count: number;
  candidate_count: number;
  approved_count: number;
  pending_ai_count: number;
  drive_ready_count: number;
  scan_next_at: string | null;
  scan_last_completed_at: string | null;
  reference_previews: SourcePlanReferencePreview[];
  last_error_code: string | null;
  analyzed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type SourcePlanOverview = {
  embroidery_groups: number;
  source_images: number;
  working_groups: number;
  refs_loaded: number;
  stage2_groups: number;
  stage2_source_images: number;
  stage2_drive_ready_refs: number;
  stage2_active_jobs: number;
};

export type SourcePlanPage = {
  items: SourcePlan[];
  page: number;
  page_size: number;
  total: number;
  overview: SourcePlanOverview;
};

export type Stage2Skill = {
  source: "local" | "openai";
  skill_id: string | null;
  skill_name: string;
  display_name: string;
  description: string;
  default_version: string | null;
  latest_version: string | null;
  local_version: string | null;
  synced_version: string | null;
  ready: boolean;
  sync_state: "ready" | "not_synced" | "update_available" | "local_conflict";
  version_options: string[];
};

export type Stage2SkillCatalog = {
  openai_configured: boolean;
  openai_status: "not_configured" | "connected" | "error";
  error_code: string | null;
  items: Stage2Skill[];
};

export type Stage2SkillSelection = {
  source: "local" | "openai";
  skill_id: string | null;
  skill_name: string;
  skill_version: string | null;
};

export type Stage2SkillRegistryVersion = {
  id: string;
  version: string;
  is_default: boolean;
  is_synced: boolean;
  status: string;
  bundle_sha256: string | null;
  created_at: string;
};

export type Stage2SkillRegistryItem = {
  id: string;
  source: "local" | "openai";
  skill_id: string | null;
  skill_name: string;
  display_name: string;
  description: string;
  workflow: string;
  enabled: boolean;
  default_version: string | null;
  latest_version: string | null;
  synced_version: string | null;
  sync_state: string;
  validation_status: string;
  bundle_sha256: string | null;
  last_error: string | null;
  versions: Stage2SkillRegistryVersion[];
  created_at: string;
  updated_at: string;
};

export type Stage2SkillRegistry = {
  can_manage: boolean;
  items: Stage2SkillRegistryItem[];
};

export type Stage2Job = {
  id: string;
  source_plan_id: string;
  campaign_id: string;
  source_revision: string;
  skill_name: string;
  skill_source: "local" | "openai";
  skill_id: string | null;
  skill_version: string | null;
  skill_bundle_sha256: string | null;
  selected_candidate_ids: string[];
  reference_count: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  can_cancel: boolean;
  cancel_available_until: string | null;
  processing_job_id: string | null;
  provider_request_id: string | null;
  output_content_type: string | null;
  output_size_bytes: number | null;
  output_width: number | null;
  output_height: number | null;
  output_remote_file_id: string | null;
  output_web_url: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Stage2JobCreated = {
  created: boolean;
  job: Stage2Job;
};

export type Stage2JobsCancelled = {
  cancelled: number;
  job_ids: string[];
};

export type Stage3AnalysisStatus =
  | "pending"
  | "queued"
  | "analyzing"
  | "ready"
  | "rejected"
  | "error";

export type Stage3ReviewImage = {
  stage2_job_id: string;
  source_plan_id: string;
  source_name: string;
  source_relative_path: string;
  output_remote_file_id: string;
  output_width: number | null;
  output_height: number | null;
  output_content_type: string | null;
  completed_at: string | null;
  preview_url: string;
  original_url: string;
  analysis_id: string | null;
  analysis_status: Stage3AnalysisStatus;
  final_score: number | null;
  mobile_ugc_score: number | null;
  photorealism_score: number | null;
  product_visibility_score: number | null;
  review_fit_score: number | null;
  person_visible: boolean | null;
  hat_visible: boolean | null;
  product_visible: boolean | null;
  embroidery_visible: boolean | null;
  scene_type: string | null;
  framing_type: string | null;
  summary: string | null;
  reviewer_name: string | null;
  star_rating: number | null;
  review_text: string | null;
  review_generated_at: string | null;
  reject_reasons: string[];
  last_error_code: string | null;
};

export type Stage3ReviewGroup = {
  folder_id: string;
  folder_name: string;
  folder_path: string;
  image_count: number;
  status: "pending" | "analyzing" | "ready" | "partial" | "rejected" | "error";
  ready_count: number;
  rejected_count: number;
  analyzing_count: number;
  pending_count: number;
  error_count: number;
  latest_completed_at: string | null;
  images: Stage3ReviewImage[];
};

export type Stage3ReviewGroupList = {
  items: Stage3ReviewGroup[];
  total_groups: number;
  total_images: number;
  ready_images: number;
  rejected_images: number;
  analyzing_images: number;
  pending_images: number;
  error_images: number;
};

export type Stage3AnalyzeResult = {
  eligible: number;
  queued: number;
  existing: number;
};

export type SourcePlanSyncResult = {
  root_folder_id: string;
  target_count: number;
  folders_scanned: number;
  images_found: number;
  plans_created: number;
  plans_updated: number;
  plans_missing: number;
  jobs_queued: number;
  unchanged: number;
};

export type ScoutAgent = {
  id: string;
  name: string;
  status: "offline" | "ready" | "busy" | "needs_login" | "error";
  active: boolean;
  client_version: string | null;
  machine_label: string | null;
  last_error_code: string | null;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type ScoutAgentCreated = ScoutAgent & {
  agent_token: string;
};

export type ScoutRun = {
  id: string;
  campaign_id: string;
  agent_id: string;
  status: "claimed" | "running" | "completed" | "needs_login" | "failed" | "cancelled";
  query: string;
  target_count: number;
  max_scroll_batches: number;
  auto_import: boolean;
  progress_before: number;
  submitted_count: number;
  created_count: number;
  existing_count: number;
  last_error_code: string | null;
  last_heartbeat_at: string | null;
  started_at: string;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CandidateStatus =
  | "discovered"
  | "analysis_queued"
  | "analyzing"
  | "approved"
  | "needs_review"
  | "analysis_failed"
  | "rejected_no_person"
  | "rejected_head_ratio"
  | "rejected_expression"
  | "rejected_existing_headwear"
  | "rejected_head_occlusion"
  | "rejected_quality"
  | "rejected_ai_risk"
  | "rejected_context"
  | "import_queued"
  | "importing"
  | "drive_ready"
  | "import_failed"
  | "rejected_duplicate";

export type AiManualLabel = "real" | "ai" | "unsure";
export type ReferenceManualLabel = "good" | "bad" | "ai";
export type ContextManualLabel = "good" | "wrong";

export type AiFeedbackCalibration = {
  active: boolean;
  real_count: number;
  ai_count: number;
  real_mean: number | null;
  ai_mean: number | null;
};

export type Candidate = {
  id: string;
  campaign_id: string;
  pin_url: string;
  image_url: string;
  alt_text: string | null;
  status: CandidateStatus;
  analysis_revision: number;
  import_revision: number;
  people_count: number | null;
  primary_head_ratio: number | null;
  smile_score: number | null;
  head_visible: boolean | null;
  existing_headwear: boolean | null;
  head_occlusion: number | null;
  mobile_ugc_score: number | null;
  phone_authenticity_score?: number | null;
  artistic_editorial_risk?: number | null;
  quality_score: number | null;
  ai_risk_score: number | null;
  ai_risk_raw_score?: number | null;
  ai_detector_confidence?: number | null;
  ai_risk_confirmed?: boolean | null;
  ai_signal_json?: Record<string, unknown> | null;
  ai_manual_label?: AiManualLabel | null;
  ai_manual_note?: string | null;
  ai_manual_reviewed_by_user_id?: string | null;
  ai_manual_reviewed_at?: string | null;
  reference_manual_label?: ReferenceManualLabel | null;
  reference_manual_note?: string | null;
  reference_manual_reviewed_by_user_id?: string | null;
  reference_manual_reviewed_at?: string | null;
  context_manual_label?: ContextManualLabel | null;
  context_manual_note?: string | null;
  context_manual_reviewed_by_user_id?: string | null;
  context_manual_reviewed_at?: string | null;
  product_fit_score: number | null;
  context_match_active?: boolean;
  context_match_score?: number | null;
  context_match_evidence?: string[];
  matched_variant_id?: string | null;
  matched_variant_name?: string | null;
  matched_color?: string | null;
  color_match_score?: number | null;
  product_shape_score?: number | null;
  final_score: number | null;
  ranking_score?: number | null;
  source_query?: string | null;
  context_feedback_adjustment?: number;
  context_feedback_direction?: "boost" | "downrank" | null;
  context_feedback_reviews?: number;
  reject_reason: string | null;
  analyzer_provider: string | null;
  analyzer_model: string | null;
  analyzer_version: string | null;
  analysis_summary: string | null;
  analyzed_at: string | null;
  content_hash: string | null;
  width: number | null;
  height: number | null;
  size_bytes: number | null;
  remote_file_id: string | null;
  remote_folder_id: string | null;
  web_url: string | null;
  last_error_code: string | null;
  created_at: string;
  updated_at: string;
};



export type ProductReferenceView =
  | "front"
  | "front_45_left"
  | "front_45_right"
  | "side_left"
  | "side_right"
  | "back"
  | "top"
  | "logo_closeup"
  | "embroidery_closeup"
  | "material_closeup";

export type ProductVariant = {
  id: string;
  product_id: string;
  source_variant_id: string;
  sku: string | null;
  name: string | null;
  color: string | null;
  size: string | null;
  price_text: string | null;
  currency: string | null;
  image_urls: string[];
  available: boolean;
  enabled: boolean;
  status: "active" | "archived";
  position: number;
  reference_count: number;
  created_at: string;
  updated_at: string;
};

export type Product = {
  id: string;
  sku: string;
  name: string;
  product_type: string;
  color: string | null;
  material: string | null;
  crown_profile: string | null;
  crown_height_mm: number | null;
  brim_style: string | null;
  brim_length_mm: number | null;
  circumference_mm: number | null;
  logo_position: string | null;
  fit_notes: string | null;
  source_url: string | null;
  source_host: string | null;
  brand: string | null;
  source_description: string | null;
  source_category: string | null;
  source_price_text: string | null;
  source_currency: string | null;
  source_images: string[];
  source_variants: Array<Record<string, unknown>>;
  variants: ProductVariant[];
  source_metadata: Record<string, unknown>;
  source_fetched_at: string | null;
  revision: number;
  status: "active" | "archived";
  reference_count: number;
  active_views: ProductReferenceView[];
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type ProductUrlImportItem = {
  source_url: string;
  status: "created" | "updated" | "failed";
  product: Product | null;
  images_found: number;
  variants_found: number;
  variant_references_imported: number;
  primary_reference_imported: boolean;
  warning: string | null;
  error_code: string | null;
  error_message: string | null;
};

export type ProductUrlImportResult = {
  items: ProductUrlImportItem[];
  created: number;
  updated: number;
  failed: number;
};

export type ProductReference = {
  id: string;
  product_id: string;
  variant_id: string | null;
  view_type: ProductReferenceView;
  version: number;
  status: "active" | "archived";
  content_hash: string;
  original_filename: string | null;
  content_type: string;
  size_bytes: number;
  width: number;
  height: number;
  image_format: string;
  remote_file_id: string | null;
  remote_folder_id: string | null;
  web_url: string | null;
  reused_storage: boolean;
  created_at: string;
  archived_at: string | null;
};

export type ProductCreateRequest = {
  sku: string;
  name: string;
  product_type: string;
  color?: string;
  material?: string;
  crown_profile?: string;
  crown_height_mm?: number;
  brim_style?: string;
  brim_length_mm?: number;
  circumference_mm?: number;
  logo_position?: string;
  fit_notes?: string;
};

export type ProductUpdateRequest = {
  name?: string;
  product_type?: string;
  color?: string | null;
  material?: string | null;
  crown_profile?: string | null;
  crown_height_mm?: number | null;
  brim_style?: string | null;
  brim_length_mm?: number | null;
  circumference_mm?: number | null;
  logo_position?: string | null;
  fit_notes?: string | null;
};

export type ReferenceAsset = {
  id: string;
  source_type: string;
  source_key: string;
  source_url: string | null;
  original_filename: string | null;
  source_campaign_id: string | null;
  source_candidate_id: string | null;
  profile_key: string | null;
  reference_type: "person" | "product" | "scene" | "detail" | "artwork" | "other";
  status: string;
  content_hash: string;
  width: number | null;
  height: number | null;
  size_bytes: number | null;
  image_format: string | null;
  tags: string[];
  themes: string[];
  quality_score: number | null;
  visual_score: number | null;
  context_score: number | null;
  usage_count: number;
  remote_file_id: string;
  remote_folder_id: string | null;
  web_url: string | null;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type ReferenceSetRecommendationItem = {
  role: string;
  required: boolean;
  reference_asset: ReferenceAsset | null;
  score: number | null;
  reasons: string[];
  candidate_count: number;
  learning_adjustment: number;
  review_approved_count: number;
  review_rejected_count: number;
};

export type ReferenceSetReuseRecommendation = {
  reference_set_id: string | null;
  reference_set_name: string | null;
  score: number | null;
  reasons: string[];
  candidate_count: number;
  review_approved_count: number;
  review_rejected_count: number;
};

export type ReferenceSetFeedback = {
  reference_set_id: string;
  reference_set_name: string;
  score: number;
  reasons: string[];
  review_approved_count: number;
  review_rejected_count: number;
};

export type ReferenceSetRecommendation = {
  campaign_id: string;
  skill_name: string;
  suggested_name: string;
  complete: boolean;
  missing_required_roles: string[];
  learning_review_count: number;
  learning_applied: boolean;
  reuse_recommendation: ReferenceSetReuseRecommendation;
  discouraged_reference_sets: ReferenceSetFeedback[];
  items: ReferenceSetRecommendationItem[];
};

export type ReferenceSetItem = {
  id: string;
  reference_asset_id: string;
  role: string;
  position: number;
  note: string | null;
  created_at: string;
  updated_at: string;
};

export type ReferenceSet = {
  id: string;
  name: string;
  campaign_id: string | null;
  profile_key: string | null;
  description: string | null;
  status: string;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  items: ReferenceSetItem[];
};

export type GenerationSkill = {
  skill_name: string;
  display_name: string;
  description: string;
  workflows: string[];
  product_types: string[];
  required_reference_roles: string[];
  optional_reference_roles: string[];
  max_references: number;
  recommended: boolean;
};

export type GenerationSkillCatalog = {
  recommended_skill_name: string | null;
  items: GenerationSkill[];
};

export type GenerationCapability = {
  enabled: boolean;
  available: boolean;
  provider: "gemini" | "codex";
  model: string;
  operation: "reference_conditioned_product_edit";
  reason: string | null;
};

export type GenerationAttempt = {
  id: string;
  campaign_id: string;
  candidate_id: string;
  product_id: string;
  product_revision: number;
  product_sku: string;
  product_name: string;
  reference_count: number;
  reference_views: ProductReferenceView[];
  reference_set_id: string | null;
  reference_roles: string[];
  generation_variant: number;
  worker_skill_version: string;
  provider: string | null;
  provider_model: string | null;
  provider_request_id: string | null;
  processing_job_id: string | null;
  status: "prepared" | "queued" | "running" | "completed" | "failed";
  output_content_type: string | null;
  output_size_bytes: number | null;
  output_width: number | null;
  output_height: number | null;
  output_remote_file_id: string | null;
  output_web_url: string | null;
  parent_attempt_id: string | null;
  correction_supervisor_result_id: string | null;
  supervisor_correction: Record<string, unknown> | null;
  review_status: "pending" | "approved" | "rejected" | null;
  review_task_id: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  export_status: "pending_review" | "export_ready" | "not_exportable" | "exported" | null;
  export_record_id: string | null;
  catalog_asset_id: string | null;
  exported_by_user_id: string | null;
  exported_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type SupervisorResult = {
  id: string;
  campaign_id: string;
  generation_attempt_id: string;
  candidate_id: string;
  product_id: string;
  supervisor_skill_version: string;
  status: "queued" | "running" | "pass" | "fail" | "needs_human_review" | "error";
  reason: string | null;
  metrics: Record<string, number | null> | null;
  expected: Record<string, unknown> | null;
  correction: Record<string, unknown> | null;
  summary: string | null;
  provider: string | null;
  provider_model: string | null;
  processing_job_id: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  can_retry: boolean;
  attempt_count: number;
  max_attempts: number;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ReviewTask = {
  id: string;
  campaign_id: string;
  campaign_name: string;
  candidate_id: string;
  product_id: string;
  product_sku: string;
  product_name: string;
  generation_attempt_id: string;
  generation_variant: number;
  supervisor_result_id: string;
  supervisor_status: "pass" | "needs_human_review";
  supervisor_reason: string | null;
  supervisor_summary: string | null;
  supervisor_metrics: Record<string, number | null> | null;
  queue_reason: "supervisor_pass" | "supervisor_needs_human_review";
  priority: "standard" | "high";
  status: "pending" | "approved" | "rejected";
  review_note: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  export_status: "pending_review" | "export_ready" | "not_exportable" | "exported";
  output_url: string;
  created_at: string;
  updated_at: string;
};

export type ReviewTaskList = {
  items: ReviewTask[];
  total: number;
  limit: number;
  offset: number;
};

export type ReviewTaskTransition = {
  transitioned: boolean;
  task: ReviewTask;
};

export type ExportRecord = {
  id: string;
  campaign_id: string;
  generation_attempt_id: string;
  review_task_id: string;
  catalog_asset_id: string;
  content_hash: string;
  content_type: string | null;
  size_bytes: number | null;
  storage_provider: string;
  remote_file_id: string;
  remote_folder_id: string | null;
  web_url: string | null;
  status: "exported";
  requested_by_user_id: string;
  exported_at: string;
  created_at: string;
  updated_at: string;
};

export type ExportList = {
  items: ExportRecord[];
  total: number;
  limit: number;
  offset: number;
};

export type BatchExportResult = {
  scanned: number;
  exported: number;
  reused: number;
  items: ExportRecord[];
};

export type CampaignExportSummary = {
  generated: number;
  review_pending: number;
  approved: number;
  rejected: number;
  export_ready: number;
  exported: number;
};

export type DeliveryDestination = {
  id: string;
  name: string;
  kind: "google_drive_folder";
  target_ref: string;
  retention_days: number;
  active: boolean;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type DeliveryItem = {
  id: string;
  export_id: string;
  catalog_asset_id: string;
  source_remote_file_id: string;
  delivered_remote_file_id: string | null;
  delivered_web_url: string | null;
  status: "pending" | "delivered" | "failed";
  last_error_code: string | null;
  last_error_message: string | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
};

export type DeliveryPackage = {
  id: string;
  campaign_id: string;
  destination_id: string;
  status: "pending" | "delivering" | "delivered" | "partial_failed" | "expired";
  export_count: number;
  delivered_count: number;
  failed_count: number;
  auto_retry_count: number;
  items: DeliveryItem[];
  started_at: string | null;
  last_retry_at: string | null;
  next_retry_at: string | null;
  delivered_at: string | null;
  expires_at: string | null;
  expired_at: string | null;
  created_at: string;
  updated_at: string;
};

export type DeliveryPackageList = {
  items: DeliveryPackage[];
  total: number;
  limit: number;
  offset: number;
};

export type CampaignDeliverySummary = {
  campaign_status: "running" | "paused" | "completed" | "stopped";
  auto_complete_on_delivery: boolean;
  completion_destination_id: string | null;
  cataloged: number;
  packages_total: number;
  packages_delivered: number;
  packages_partial_failed: number;
  packages_expired: number;
  latest_delivered_count: number;
  latest_export_count: number;
  auto_complete_eligible: boolean;
  completed_at: string | null;
};

export type DeliveryLifecycleReconcile = {
  scanned: number;
  expired: number;
};

export type DeliveryEvent = {
  id: string;
  campaign_id: string;
  package_id: string | null;
  event_type: string;
  severity: "info" | "warning" | "error";
  message: string;
  payload: Record<string, unknown> | null;
  created_at: string;
};

export type DeliveryOperationsSummary = {
  automation_enabled: boolean;
  campaigns_total: number;
  campaigns_completed: number;
  destinations_active: number;
  packages_total: number;
  packages_delivered: number;
  packages_partial_failed: number;
  packages_expired: number;
  retry_due: number;
  retry_exhausted: number;
  items_delivered: number;
  items_failed: number;
  latest_delivery_at: string | null;
  maintenance_interval_seconds: number;
  auto_retry_max_attempts: number;
  recent_events: DeliveryEvent[];
};

export type DeliveryMaintenanceEnqueue = {
  created: boolean;
  job_id: string | null;
};

export type CampaignCreateRequest = {
  name: string;
  query: string;
  search_queries: string[];
  discovery_mode: "keyword" | "product_context";
  product_context?: {
    auto_context: boolean;
    themes: string[];
    preferred_scenes: string[];
    avoid: string[];
    notes?: string | null;
  } | null;
  target_count: number;
  max_scroll_batches: number;
  auto_import: boolean;
  auto_scout: boolean;
  scan_interval_seconds: number;
  min_head_ratio: number;
  max_head_ratio: number;
  min_smile_score: number;
  max_head_occlusion: number;
  max_ai_risk_score: number;
  min_quality_score: number;
  min_ugc_score: number;
  min_product_fit_score: number;
  require_head_visible: boolean;
  reject_headwear: boolean;
};

export type CampaignUpdateRequest = Partial<Omit<CampaignCreateRequest, "query">>;
