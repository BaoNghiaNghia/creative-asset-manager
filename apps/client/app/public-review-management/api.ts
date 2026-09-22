export type Scope = {
  external_source_id: string;
  folder_external_id: string;
  folder_name?: string | null;
};

export type Share = {
  id: string;
  public_id: string;
  name: string;
  status: string;
  allow_comments: boolean;
  allow_download: boolean;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
  scopes: Scope[];
  share_url?: string;
};

export type CurrentShareLink = {
  share_url: string;
  expires_at: string | null;
};

const root = "/api/v1/public-review/shares";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(root + path, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) throw new Error("Unable to manage review share");
  return response.json();
}

export function activeShareFolderIds(
  shares: readonly Share[],
  now = Date.now(),
): Map<string, string> {
  const result = new Map<string, string>();
  for (const share of shares) {
    if (share.status !== "active" || share.revoked_at) continue;
    if (share.expires_at && Date.parse(share.expires_at) <= now) continue;
    for (const scope of share.scopes) {
      result.set(
        scope.external_source_id + ":" + scope.folder_external_id,
        share.id,
      );
    }
  }
  return result;
}

export const managementApi = {
  list: () => call<{ items: Share[] }>(""),
  create: (
    value: Pick<
      Share,
      "name" | "allow_comments" | "allow_download" | "expires_at"
    > & { scopes: Scope[] },
  ) => call<Share>("", { method: "POST", body: JSON.stringify(value) }),
  update: (
    id: string,
    value: Partial<
      Pick<
        Share,
        "name" | "allow_comments" | "allow_download" | "expires_at" | "scopes"
      >
    >,
  ) => call<Share>("/" + encodeURIComponent(id), {
    method: "PATCH",
    body: JSON.stringify(value),
  }),
  current: (id: string) => call<CurrentShareLink>(
    "/" + encodeURIComponent(id) + "/current-link",
  ),
  rotate: (id: string) => call<Share>(
    "/" + encodeURIComponent(id) + "/rotate-secret",
    { method: "POST" },
  ),
  revoke: (id: string) => call<Share>(
    "/" + encodeURIComponent(id),
    { method: "DELETE" },
  ),
};
