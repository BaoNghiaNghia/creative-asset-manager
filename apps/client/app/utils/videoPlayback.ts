export type ExplorerPlaybackItem = {
  provider?: "google-drive" | "onedrive" | "sharepoint" | null;
  id?: string | null;
  source_type?: string | null;
  external_source_id?: string | null;
  external_asset_id?: string | null;
};

export type ExplorerPlaybackTicket = {
  url: string;
  cdn: boolean;
  expires_at: number | null;
};

const TICKET_EXPIRY_SAFETY_SECONDS = 15;
const FALLBACK_TICKET_TTL_SECONDS = 4;
const ticketCache = new Map<string, { ticket: ExplorerPlaybackTicket; reusableUntil: number }>();
const pendingTickets = new Map<string, Promise<ExplorerPlaybackTicket>>();

export function playbackProvider(sourceType: string | null | undefined): "google-drive" | "onedrive" | "sharepoint" | null {
  if (sourceType === "google_drive") return "google-drive";
  if (sourceType === "onedrive") return "onedrive";
  if (sourceType === "sharepoint") return "sharepoint";
  return null;
}

function playbackIdentity(item: ExplorerPlaybackItem) {
  const provider = item.provider || playbackProvider(item.source_type);
  const assetId = item.external_asset_id || item.id || null;
  const sourceId = item.external_source_id || null;
  if (!provider || !assetId || !sourceId) return null;
  return { provider, assetId, sourceId };
}

function playbackKey(item: ExplorerPlaybackItem) {
  const identity = playbackIdentity(item);
  return identity ? identity.provider + ":" + identity.sourceId + ":" + identity.assetId : null;
}

export function buildVideoPlaybackUrl(item: ExplorerPlaybackItem): string | null {
  const identity = playbackIdentity(item);
  if (!identity) return null;
  const params = new URLSearchParams({
    provider: identity.provider,
    external_source_id: identity.sourceId,
  });
  return "/api/explorer/media/" + encodeURIComponent(identity.assetId) + "?" + params.toString();
}

export function buildVideoPlaybackTicketUrl(item: ExplorerPlaybackItem): string | null {
  const identity = playbackIdentity(item);
  if (!identity) return null;
  const params = new URLSearchParams({
    provider: identity.provider,
    external_source_id: identity.sourceId,
  });
  return "/api/explorer/media/" + encodeURIComponent(identity.assetId) + "/playback-ticket?" + params.toString();
}

export function explorerPlaybackTicketReusable(
  ticket: ExplorerPlaybackTicket,
  nowSeconds = Date.now() / 1000,
) {
  return Boolean(
    ticket.cdn
    && ticket.expires_at !== null
    && ticket.expires_at > nowSeconds + TICKET_EXPIRY_SAFETY_SECONDS,
  );
}

export function invalidateExplorerPlaybackTicket(item: ExplorerPlaybackItem) {
  const key = playbackKey(item);
  if (key) ticketCache.delete(key);
}

export function getExplorerPlaybackTicket(
  item: ExplorerPlaybackItem,
  force = false,
): Promise<ExplorerPlaybackTicket> {
  const key = playbackKey(item);
  const endpoint = buildVideoPlaybackTicketUrl(item);
  if (!key || !endpoint) return Promise.reject(new Error("Video playback identity is unavailable."));
  if (force) ticketCache.delete(key);
  if (!force) {
    const cached = ticketCache.get(key);
    const now = Date.now() / 1000;
    if (cached && cached.reusableUntil > now) return Promise.resolve(cached.ticket);
    if (cached) ticketCache.delete(key);
    const pending = pendingTickets.get(key);
    if (pending) return pending;
  }

  const request = fetch(endpoint, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  }).then(async response => {
    if (!response.ok) throw new Error("Unable to prepare video playback.");
    const value = await response.json() as Partial<ExplorerPlaybackTicket>;
    if (typeof value.url !== "string" || typeof value.cdn !== "boolean") {
      throw new Error("Invalid video playback ticket.");
    }
    const ticket: ExplorerPlaybackTicket = {
      url: value.url,
      cdn: value.cdn,
      expires_at: typeof value.expires_at === "number" ? value.expires_at : null,
    };
    const now = Date.now() / 1000;
    if (explorerPlaybackTicketReusable(ticket, now)) {
      ticketCache.set(key, {
        ticket,
        reusableUntil: ticket.expires_at! - TICKET_EXPIRY_SAFETY_SECONDS,
      });
    } else if (!ticket.cdn) {
      ticketCache.set(key, { ticket, reusableUntil: now + FALLBACK_TICKET_TTL_SECONDS });
    } else {
      ticketCache.delete(key);
    }
    return ticket;
  }).finally(() => {
    if (pendingTickets.get(key) === request) pendingTickets.delete(key);
  });
  pendingTickets.set(key, request);
  return request;
}

export function resolveExplorerPlaybackUrl(
  item: ExplorerPlaybackItem,
  force = false,
): Promise<string | null> {
  const fallback = buildVideoPlaybackUrl(item);
  if (!fallback) return Promise.resolve(null);
  return getExplorerPlaybackTicket(item, force)
    .then(ticket => ticket.url)
    .catch(() => fallback);
}

export function prefetchExplorerPlaybackTicket(item: ExplorerPlaybackItem) {
  return getExplorerPlaybackTicket(item).then(() => undefined);
}

export function playbackSeekSeconds(startMs: number, duration: number): number {
  const target = Number.isFinite(startMs) ? Math.max(0, startMs / 1000) : 0;
  return Number.isFinite(duration) && duration >= 0 ? Math.min(target, duration) : target;
}

export function seekVideoAt(
  video: Pick<HTMLVideoElement, "currentTime" | "duration">,
  startMs: number,
): number {
  const target = playbackSeekSeconds(startMs, video.duration);
  video.currentTime = target;
  return target;
}
