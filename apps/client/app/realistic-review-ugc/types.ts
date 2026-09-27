export type Campaign = {
  id: string;
  name: string;
  query: string;
  target_count: number;
  max_scroll_batches: number;
  auto_import: boolean;
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
  product_revision: number | null;
  product_reference_count: number;
  product_reference_views: ProductReferenceView[];
  product_bound_at: string | null;
  product_binding_stale: boolean;
  generation_ready: boolean;
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

export type CandidateStatus =
  | "discovered"
  | "analysis_queued"
  | "analyzing"
  | "approved"
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
  quality_score: number | null;
  ai_risk_score: number | null;
  product_fit_score: number | null;
  final_score: number | null;
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
  revision: number;
  status: "active" | "archived";
  reference_count: number;
  active_views: ProductReferenceView[];
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type ProductReference = {
  id: string;
  product_id: string;
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

export type GenerationCapability = {
  enabled: boolean;
  available: boolean;
  provider: "gemini";
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
  export_status: "pending_review" | "export_ready" | "not_exportable" | null;
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
  export_status: "pending_review" | "export_ready" | "not_exportable";
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

export type CampaignCreateRequest = {
  name: string;
  query: string;
  target_count: number;
  max_scroll_batches: number;
  auto_import: boolean;
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
