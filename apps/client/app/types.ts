export type Asset = {
  id: string;
  name: string;
  kind: "folder" | "image" | "video" | "pdf" | "document" | "other";
  mime_type: string;
  modified_at?: string;
  thumbnail_url?: string;
  web_url?: string;
};

export type Tag = { id: string; name: string; color: string };
export type GoogleUser = { id: string; name?: string; email?: string; picture?: string };
export type AuthState = { authenticated: boolean; user: GoogleUser | null; checking: boolean };
export type OAuthErrorState = { message: string; requestId?: string } | null;
export type Folder = { parent: Asset; children: Asset[] };
export type TreeCache = Record<string, Asset[]>;
