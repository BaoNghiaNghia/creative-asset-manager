import type { Asset, PlaybackTicket } from "./api";

const TICKET_EXPIRY_SAFETY_SECONDS = 15;
const FALLBACK_TICKET_TTL_SECONDS = 4;

function assetKey(asset: Pick<Asset, "asset_id" | "source_asset_id">) {
  return asset.asset_id + ":" + asset.source_asset_id;
}

export function reviewPlaybackTicketReusable(
  ticket: PlaybackTicket,
  nowSeconds = Date.now() / 1000,
) {
  return Boolean(
    ticket.cdn
    && ticket.expires_at !== null
    && ticket.expires_at > nowSeconds + TICKET_EXPIRY_SAFETY_SECONDS,
  );
}

export function reviewMediaOrigin(ticket: PlaybackTicket) {
  if (!ticket.cdn) return null;
  try {
    return new URL(ticket.url).origin;
  } catch {
    return null;
  }
}

export function createReviewPlaybackTicketCache(
  fetchTicket: (asset: Asset) => Promise<PlaybackTicket>,
  options?: {
    nowSeconds?: () => number;
    onTicket?: (ticket: PlaybackTicket) => void;
    fallbackTtlSeconds?: number;
  },
) {
  const cached = new Map<string, { ticket: PlaybackTicket; reusableUntil: number }>();
  const pending = new Map<string, Promise<PlaybackTicket>>();
  const nowSeconds = options?.nowSeconds || (() => Date.now() / 1000);
  const fallbackTtlSeconds = Math.max(0, options?.fallbackTtlSeconds ?? FALLBACK_TICKET_TTL_SECONDS);

  const peek = (asset: Pick<Asset, "asset_id" | "source_asset_id">) => {
    const key = assetKey(asset);
    const entry = cached.get(key);
    if (!entry) return undefined;
    if (entry.reusableUntil > nowSeconds()) return entry.ticket;
    cached.delete(key);
    return undefined;
  };

  const get = (asset: Asset, force = false) => {
    const key = assetKey(asset);
    if (force) cached.delete(key);
    if (!force) {
      const ticket = peek(asset);
      if (ticket) return Promise.resolve(ticket);
      const active = pending.get(key);
      if (active) return active;
    }

    const request = fetchTicket(asset)
      .then(ticket => {
        const now = nowSeconds();
        if (reviewPlaybackTicketReusable(ticket, now)) {
          cached.set(key, {
            ticket,
            reusableUntil: ticket.expires_at! - TICKET_EXPIRY_SAFETY_SECONDS,
          });
        } else if (!ticket.cdn && fallbackTtlSeconds > 0) {
          cached.set(key, { ticket, reusableUntil: now + fallbackTtlSeconds });
        } else {
          cached.delete(key);
        }
        options?.onTicket?.(ticket);
        return ticket;
      })
      .finally(() => {
        if (pending.get(key) === request) pending.delete(key);
      });
    pending.set(key, request);
    return request;
  };

  return {
    get,
    peek,
    prefetch(asset: Asset) {
      return get(asset).then(() => undefined);
    },
    invalidate(asset: Pick<Asset, "asset_id" | "source_asset_id">) {
      cached.delete(assetKey(asset));
    },
    size() {
      return cached.size;
    },
  };
}

export function createReviewPrewarmQueue(
  run: (asset: Asset) => Promise<unknown>,
  concurrency = 2,
  maxQueued = 12,
) {
  const limit = Math.max(1, Math.floor(concurrency));
  const queueLimit = Math.max(1, Math.floor(maxQueued));
  const queue: Array<{ asset: Asset; priority: "normal" | "high" }> = [];
  const scheduled = new Set<string>();
  const completed = new Set<string>();
  let active = 0;

  const drain = () => {
    while (active < limit && queue.length) {
      const { asset } = queue.shift()!;
      const key = assetKey(asset);
      active += 1;
      void run(asset)
        .then(() => completed.add(key))
        .catch(() => undefined)
        .finally(() => {
          active -= 1;
          scheduled.delete(key);
          drain();
        });
    }
  };

  return {
    enqueue(asset: Asset, priority: "normal" | "high" = "normal") {
      const key = assetKey(asset);
      if (completed.has(key) || scheduled.has(key)) return false;
      if (queue.length >= queueLimit) {
        if (priority === "normal") return false;
        const dropped = queue.pop();
        if (dropped) scheduled.delete(assetKey(dropped.asset));
      }
      scheduled.add(key);
      if (priority === "high") {
        const firstNormal = queue.findIndex(item => item.priority === "normal");
        queue.splice(firstNormal < 0 ? queue.length : firstNormal, 0, { asset, priority });
      } else {
        queue.push({ asset, priority });
      }
      drain();
      return true;
    },
    hasCompleted(asset: Pick<Asset, "asset_id" | "source_asset_id">) {
      return completed.has(assetKey(asset));
    },
    snapshot() {
      return { active, queued: queue.length, completed: completed.size };
    },
  };
}
