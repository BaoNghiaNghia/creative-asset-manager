export type Provider = "google-drive" | "sharepoint";
export type VisibilityFilter = "all" | "public" | "draft";
export type AssetProcessingStatus =
  | "discovered"
  | "stored"
  | "analyzing"
  | "metadata_ready"
  | "search_pending"
  | "indexing"
  | "indexed"
  | "search_failed"
  | "duplicate"
  | "failed";


export type Asset = {
  provider: Provider;
  id: string;
  name: string;
  kind: "folder" | "image" | "video" | "pdf" | "document" | "other";
  mime_type: string;
  parent_id?: string;
  size?: number;
  modified_at?: string;
  thumbnail_url?: string;
  web_url?: string;
  ancestor_ids?: string[];
  ancestor_names?: string[];
  internal_asset_id?: string;
  source_asset_id?: string;
  external_source_id?: string;
  folder_path?: string;
  location_breadcrumb?: Array<{ id: string; name: string }>;
  location_unavailable?: boolean;
  location_status?: "resolved" | "unavailable";
  score?: number;
  has_children?: boolean;
};

export type Tag = {
  id: string;
  name: string;
  color: string;
  group_key?: string;
  is_system?: boolean;
};
export type AssetMetadata = {
  item_id: string;
  tag_ids: string[];
  rating: number | null;
  processing_status: AssetProcessingStatus;
};
export type AssetMetadataMap = Record<string, AssetMetadata>;
export type CloudUser = { id: string; name?: string; email?: string; picture?: string };
export type AuthState = { authenticated: boolean; user: CloudUser | null; checking: boolean };
export type OAuthErrorState = { message: string; requestId?: string } | null;
export type Folder = {
  parent: Asset;
  children: Asset[];
  next_page_token?: string | null;
  has_more?: boolean;
};
export type TreeCache = Record<string, Asset[]>;


export type DriveIndexStatus = {
  state: "idle" | "running" | "completed" | "failed";
  status: string;
  progress: number;
  indexed_count: number;
  processed_folders: number;
  pending_folders: number;
  skipped_folders: number;
  started_at?: string;
  completed_at?: string;
  error?: string;
};


export type ProviderSessions = Record<Provider, AuthState>;

export type ViewerBootstrapFolder = {
  id: string;
  name: string;
  external_source_id: string;
};
export type ViewerBootstrapSource = {
  external_source_id: string;
  display_name: string;
  folders: ViewerBootstrapFolder[];
};
export type ViewerBootstrap = {
  sources: ViewerBootstrapSource[];
  auto_selected_source_id: string | null;
  auto_selected_folder_id: string | null;
};

export type SearchCapabilities = {
  selected_version: "v3";
  readiness: "ready" | "verification_unknown" | "incompatible" | "unavailable";
  search_available: boolean;
  viewer_scoped: boolean;
  failure_code: string | null;
  facet_names: string[];
  examples: string[];
};
export type SearchFacetBucket = { value: string; count: number };
export type SearchSuggestion = {
  text: string;
  prefix: string;
  completion: string;
  kind: "filename" | "visible_text" | "search_text";
};
export type ParsedQueryDebug = {
  mode: string;
  clauses: Array<{ kind: string; field?: string | null; value: string }>;
};
export type AssetDetails = {
  asset: Record<string, unknown>;
  sources: Array<Record<string, unknown>>;
  storage: Array<Record<string, unknown>>;
  active_analysis: Record<string, any> | null;
  analysis_history: Array<Record<string, any>>;
  analysis_total: number;
  jobs: Array<Record<string, any>>;
  job_total: number;
  pipelines: Array<Record<string, any>>;
  lifecycle_status: string;
  location_breadcrumb?: Array<{ id: string; name: string }>;
  location_unavailable?: boolean;
  location_status?: "resolved" | "unavailable";
  can_administer: boolean;
  limits: { max_json_nodes: number; max_json_depth: number };
};
