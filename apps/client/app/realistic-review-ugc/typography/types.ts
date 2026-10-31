/** Pilot G2 Typography Ideas metadata; never treat a preview as a generated design. */
export type TypographyEditorialStatus = "candidate" | "approved" | "rejected" | "archived";
export type TypographyAnalysisStatus = "pending" | "running" | "ready" | "failed" | "needs_review";
export type TypographyRights = "unknown" | "owned" | "licensed" | "permitted_reference" | "blocked";
export type TypographyPrivacy = "private" | "workspace";
export type TypographyScope = "workspace" | "campaign" | "keyword" | "skill";
export type TypographyStyleRole = "layout" | "font_mood" | "overlap";
export type TypographySourceType = "upload" | "scout" | "internal";

export type TypographyReference = {
  id: string;
  asset_id: string;
  filename: string | null;
  content_hash: string;
  source_type: TypographySourceType;
  source_url: string | null;
  source_platform: string | null;
  source_rights_status: TypographyRights;
  source_attribution: string | null;
  privacy: TypographyPrivacy;
  scope: TypographyScope;
  campaign_id: string | null;
  keyword_id: string | null;
  skill_name: string | null;
  reference_roles: TypographyStyleRole[];
  influence: "balanced";
  preference: "positive" | "negative" | "neutral";
  priority: number;
  tags: string[];
  notes: string | null;
  analysis_status: TypographyAnalysisStatus;
  editorial_status: TypographyEditorialStatus;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  image_url: string;
};

export type TypographyReferencePage = {
  items: TypographyReference[];
  total: number;
  limit: number;
  offset: number;
  capabilities: { read: boolean; upload: boolean; manage: boolean };
  stats: { total: number; approved: number; needs_review: number; analyzing: number };
};
export type TypographyReferenceFilters = {
  query?: string;
  status?: TypographyEditorialStatus | "";
  sourceType?: TypographySourceType | "";
  scope?: TypographyScope | "";
  styleRole?: TypographyStyleRole | "";
  sort?: "newest" | "oldest" | "priority";
  myUploadsOnly?: boolean;
  offset?: number;
  limit?: number;
};
export type TypographyUploadResult = {
  index: number;
  filename: string;
  status: "created" | "duplicate" | "error";
  code?: string;
  reference?: TypographyReference;
  stored_new_asset?: boolean;
};
export type TypographyUploadBatch = {
  items: TypographyUploadResult[];
  total: number;
  created: number;
  duplicates: number;
  failed: number;
};
export type TypographyReferencePatch = {
  editorial_status?: TypographyEditorialStatus;
  source_rights_status?: TypographyRights;
  privacy?: TypographyPrivacy;
  scope?: TypographyScope;
  campaign_id?: string | null;
  keyword_id?: string | null;
  skill_name?: string | null;
  preference?: "positive" | "negative" | "neutral";
  priority?: number;
  tags?: string[];
  reference_roles?: TypographyStyleRole[];
  notes?: string | null;
  source_attribution?: string | null;
};
export type TypographyDna = {
  id: string;
  schema_version: string;
  analyzer_version: string;
  style_family: string;
  typography_region: Record<string, unknown>;
  font_mood: Record<string, unknown>;
  layout_graph: Record<string, unknown>;
  overlap_rules: unknown[];
  confidence: number;
  analyzed_at: string;
  review?: {
    decision: "pending" | "accepted" | "needs_review" | "rejected";
    reason_code?: string | null;
    reviewed_at?: string | null;
    has_override?: boolean;
    effective_dna?: Record<string, unknown> | null;
  };
};

export type TypographyAnalysisState = {
  reference_id: string;
  analyzer_version: string;
  analysis_status: TypographyAnalysisStatus;
  dna_available: boolean;
  dna_review_status: "pending" | "accepted" | "needs_review" | "rejected";
  worker_enabled: boolean;
  can_request_analysis: boolean;
  analysis_blocker: "rights_unverified" | "reference_inactive" | "worker_disabled" | null;
  job: null | {
    id: string;
    status: "queued" | "running" | "completed" | "failed" | "needs_review";
    attempt_count: number;
    next_attempt_at: string | null;
    last_error_code: string | null;
  };
};

/** Zero-provider, non-persisted Style Lab SVGs; NOT Stage 1 generated outputs. */
export type TypographySvgPreview = {
  id: string;
  preview_version: string;
  source_keyword_exact: string;
  reference_id: string;
  dna_id: string;
  layout: string;
  font_family: string;
  influence: "balanced";
  style_components: string[];
  svg: string;
  transparent_background: boolean;
  preview_only: true;
  eligible_for_generation: false;
  qa: { exact_keyword: boolean; status: "preview_only"; requires_human_review: boolean };
};
export type TypographyPreviewResponse = {
  reference_id: string;
  keyword_id: string;
  source_keyword_exact: string;
  preview_only: true;
  job_created: false;
  provider_calls: 0;
  generation_plan?: { style_brief: string };
  items: TypographySvgPreview[];
};