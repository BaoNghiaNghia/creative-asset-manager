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
