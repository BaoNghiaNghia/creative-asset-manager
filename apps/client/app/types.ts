export type Asset = {
  id: string;
  name: string;
  kind: "folder" | "image" | "video" | "pdf" | "document" | "other";
  mime_type: string;
  modified_at?: string;
  thumbnail_url?: string;
  web_url?: string;
  ancestor_ids?: string[];
  ancestor_names?: string[];
};

export type SearchResponse = {
  items: Asset[];
  indexed_count: number;
  index_source: "directus" | "memory";
  truncated: boolean;
  skipped_folders: number;
};

export type Tag = { id: string; name: string; color: string };
export type GoogleUser = { id: string; name?: string; email?: string; picture?: string };
export type AuthState = { authenticated: boolean; user: GoogleUser | null; checking: boolean };
export type OAuthErrorState = { message: string; requestId?: string } | null;
export type Folder = { parent: Asset; children: Asset[] };
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
