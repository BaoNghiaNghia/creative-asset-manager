export type Campaign = {
  id: string;
  name: string;
  query: string;
  target_count: number;
  max_scroll_batches: number;
  auto_import: boolean;
  status: "running" | "paused" | "completed" | "stopped";
  scout_status: "offline" | "ready" | "busy" | "needs_login" | "error";
  scout_last_seen_at: string | null;
  discovered: number;
  drive_ready: number;
  failed: number;
  created_at: string;
  updated_at: string;
};

export type CampaignCreated = Campaign & { scout_token: string };

export type Candidate = {
  id: string;
  campaign_id: string;
  pin_url: string;
  image_url: string;
  alt_text: string | null;
  status: "discovered" | "importing" | "drive_ready" | "import_failed" | "rejected_duplicate";
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
};
