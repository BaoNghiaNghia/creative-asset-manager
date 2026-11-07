export type SpyTab = "products" | "keywords" | "trademarks" | "logs";
export type Page<T> = {
  items: T[]; page: number; page_size: number; total: number; pages: number;
  last_synced_at: string | null; source_status: string;
};
export type AgentStatus = {
  id: string; name: string; active: boolean;
  machine_label: string | null; last_seen_at: string | null;
  last_synced_at: string | null; extension_version: string | null;
  local_api_version: string | null;
};
export type Summary = {
  keywords: number; listings: number; picked: number; sessions: number;
  search: number; shop: number; trademarks: number; tm_pending: number; tm_checked?: number;
  errors: number; warnings: number; online_agents: number; agent_count: number;
  source_status: "online" | "offline" | "sync_delayed" | "never_synced";
  last_synced_at: string | null; last_seen_at: string | null;
  last_inventory_at?: string | null;
};
export type ObservedDayBucket = { day: string; count: number };
export type ObservedDaysSummary = {
  timezone: "Asia/Ho_Chi_Minh"; start_hour: 6; items: ObservedDayBucket[];
};
export type Session = {
  id: string; status: string | null; keyword: string | null;
  phase: string | null; revision: number; startedAt: string | null; updatedAt: string | null;
};
export type Mark = {
  markText: string; serialNumber: string; liveStatus: string;
  type?: string; filedOn?: string; registeredOn?: string; source?: string;
};
export type Listing = {
  sessionId: string; keyword: string; sourceKind: string; sourceKinds?: string[]; listingKey: string;
  picked: boolean;
  pageTitle: string; url: string | null; badge: string | null;
  thumbnailUrl?: string | null; fullImageUrl?: string | null;
  searchBadge: string | null; bought24h: number | null; views24h: number | null;
  inCarts: number | null; demandScore: number | null; observedAt: string | null;
  keywordSms?: string[]; keywordSmsQuery?: string;
  keywordSmsStatus?: string; keywordSmsCheckedAt?: string | null;
  metadata?: {sourceKinds: string[]; keywordAliases: string[];
    occurrences: number | null; keywordCount: number | null; sessionCount: number | null;
    keywordSms?: string[]; keywordSmsQuery?: string; keywordSmsStatus?: string;
    thumbnailUrl?: string | null; fullImageUrl?: string | null;} | null;
  tm: string; tmStatus: string; tmSource: string | null; tmCheckedAt: string | null;
  tmSourceAsOf: string | null; tmMatches?: Mark[] | null;
};
export type Keyword = {
  sessionId: string; id: string; parentId: string | null; keyword: string;
  status: string | null; depth: number | null; priority: number | null;
  reason: string | null; evidence: unknown; matchedCount: number | null;
  sourceUrl: string | null; updatedAt?: string | null;
};
export type Trademark = {
  checkId: string; kind: string; keyword: string | null; title: string | null;
  status: string | null; risk: string | null; liveCount: number; deadCount: number;
  unknownCount: number; matched?: Mark[] | null; checkedAt: string | null;
  source: string | null; sourceAsOf: string | null;
};
export type SpyLog = {
  eventId: string; sessionId: string | null; at: string; level: string | null;
  source: string | null; phase: string | null; keyword: string | null;
  category: string | null; action: string | null; message: string | null;
  stack: string | null;
};

const ROOT = "/api/v1/etsy-spy";
export async function spyFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(ROOT + path, {
    credentials: "include",
    cache: "no-store",
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let error = "HTTP " + response.status;
    try {
      const payload = await response.json();
      error = typeof payload?.detail === "string" ? payload.detail
        : payload?.detail?.message || error;
    } catch { /* Proxy may return an HTML error. */ }
    throw new Error(error);
  }
  return response.json() as Promise<T>;
}
export function csvPath(tab: "products" | "logs", query: URLSearchParams) {
  return ROOT + (tab === "logs" ? "/logs/export.csv" : "/export.csv") +
    "?" + query.toString();
}
export function readableTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short", timeStyle: "medium", timeZone: "Asia/Ho_Chi_Minh",
  }).format(date);
}