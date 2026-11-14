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
export type ImageAgentStatus = {
  id: string; name: string; active: boolean;
  last_seen_at: string | null;
};
export type ExtractionOverview = {
  enabled: boolean; daily_cap: number; claimed_today: number;
  remaining_today: number; queued: number; processing: number;
  completed_today: number; needs_review_today: number;
  needs_ai_today: number; failed_today: number;
  timezone: "Asia/Ho_Chi_Minh";
  storage?: {
    ready: boolean; reason: string; used_bytes: number;
    quota_bytes: number; free_bytes: number | null;
  };
};
export type Summary = {
  keywords: number; listings: number; picked: number; later?: number; later_seeds?: number; sessions: number;
  search: number; shop: number; trademarks: number; tm_pending: number; tm_checked?: number;
  errors: number; warnings: number; online_agents: number; agent_count: number;
  source_status: "online" | "offline" | "sync_delayed" | "never_synced";
  last_synced_at: string | null; last_seen_at: string | null;
  last_inventory_at?: string | null;
  picked_images?: { checked: number; complete: number; missing: number };
};
export type ObservedDayBucket = { day: string; count: number; pickedCount: number };
export type ListingScope = "eligible" | "picked" | "later";
export type ObservedDaysSummary = {
  pick_state: ListingScope;
  timezone: "Asia/Ho_Chi_Minh"; start_hour: 6; items: ObservedDayBucket[];
};
export type DayGroup = { key: string; label: string; count: number };
export type DayGroupsSummary = {
  day: string; pick_state: ListingScope;
  timezone: "Asia/Ho_Chi_Minh"; start_hour: 6;
  total: number; items: DayGroup[];
};
export type Session = {
  id: string; status: string | null; keyword: string | null;
  phase: string | null; revision: number; startedAt: string | null; updatedAt: string | null;
};
export type Mark = {
  markText: string; serialNumber: string; liveStatus: string;
  type?: string; filedOn?: string; registeredOn?: string; source?: string;
};
export type DesignAsset = {
  id: string; job_id: string; listing_key: string;
  position: string; variant: number; method: string;
  status: string; source: string; output_sha256: string;
  thumbnail_url: string; original_url: string;
  print_ready: false; rights_status: "unverified";
  manual_qa_required: true; created_at?: string | null;
};
export type TikTokListingDraft = {
  title: string; description: string; highlights: string[]; search_terms: string[];
  qa_status: "needs_fact_check"; keyword_source: string;
  skill_version?: string;
  ytrends_status: "connected" | "no_data" | "unavailable" | "disabled";
  ytrends_evidence?: {
    status: string; source: string; marketplace: "etsy"; query: string;
    as_of_date?: string | null; median_data_age_days?: number | null;
    total_listings?: number | null; total_sellers?: number | null;
    competition_level?: string | null; opportunity_score?: number | null;
    dataset_note: string;
    refreshed_at?: string; last_checked_at?: string;
    last_refresh_status?: string; research_revision?: string;
    keywords?: Array<{ keyword: string; status: string; listings: number | null;
      competition: string | null; as_of_date: string | null }>;
  };
  warnings: string[]; published: false;
  metrics: { title_characters: number; description_characters: number;
    description_words: number; highlights_count: number;
    highlights_characters_total: number; search_terms_count: number;
    search_terms_characters_total: number; search_terms_joined_characters: number; };
};
export type TikTokListingView = {
  listingKey?: string; enabled?: boolean; status: string;
  job_id?: string | null; draft_only: true;
  title_preview?: string | null; ytrends_status?: string; qa_status?: string | null;
  error_code?: string | null;
  generation_engine?: "codex" | "gemini"; generation_provider?: string | null;
  regeneration_count?: number; can_regenerate?: boolean;
  previous_job_id?: string; previous_draft?: TikTokListingDraft;
  result?: TikTokListingDraft;
};
export type Listing = {
  sessionId: string; keyword: string; sourceKind: string; sourceKinds?: string[]; listingKey: string;
  picked: boolean;
  designs?: DesignAsset[];
  tiktokListing?: TikTokListingView;
  smsPush?: { status: string; code?: string | null; sentAt?: string | null;
    updatedAt?: string | null } | null;
  extraction?: { job_id?: string | null; status: string; engine?: string;
    manual_qa_required?: boolean; reason?: string | null;
    review_flags?: string[]; upscale_factor?: number | null;
    print_ready?: boolean; rights_status?: string;
    candidate_ready?: boolean; candidate_method?: string | null; };
  later?: boolean; laterManual?: boolean; laterReason?: string | null;
  laterScore?: number | null; laterSeedKey?: string | null;
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
const RETRYABLE_READ_STATUS = new Set([502, 503, 504]);

async function waitForReadRetry(ms: number, signal?: AbortSignal | null): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Request aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, ms);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

export async function spyFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const safeRead = method === "GET" && !init?.body;
  const request: RequestInit = {
    credentials: "include",
    cache: "no-store",
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  };
  for (let attempt = 0; ; attempt += 1) {
    const response = await fetch(ROOT + path, request);
    // One bounded retry for transient gateway overload. Never retry writes
    // (Pick, Later, SMS, Job actions) or authentication/permission failures.
    if (safeRead && attempt === 0 && RETRYABLE_READ_STATUS.has(response.status)) {
      const retryAfterSeconds = Number(response.headers.get("Retry-After"));
      const delay = retryAfterSeconds > 0 && Number.isFinite(retryAfterSeconds)
        ? Math.min(4000, retryAfterSeconds * 1000)
        : 1200 + Math.floor(Math.random() * 500);
      await waitForReadRetry(delay, init?.signal);
      continue;
    }
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