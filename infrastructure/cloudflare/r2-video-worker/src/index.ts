import { cacheKeyFromPath } from "./path";
import { parseSingleByteRange } from "./range";
import { verifyReadPath } from "./signature";

export interface R2ReadObject {
  size: number;
  body?: ReadableStream<Uint8Array>;
  httpEtag?: string;
  uploaded?: Date;
  httpMetadata?: { contentType?: string };
}

export interface ReadOnlyR2Bucket {
  get(key: string, options?: { range?: { offset: number; length: number } }): Promise<R2ReadObject | null>;
  head(key: string): Promise<R2ReadObject | null>;
}

export interface EdgeCache {
  match(request: Request): Promise<Response | undefined>;
  put(request: Request, response: Response): Promise<void>;
}

export interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
}

export interface Env {
  VIDEO_CACHE_BUCKET: ReadOnlyR2Bucket;
  R2_VIDEO_MEDIA_SIGNING_SECRET: string;
  R2_VIDEO_MEDIA_MAX_TTL_SECONDS?: string;
  R2_VIDEO_MEDIA_AUTH_DIAGNOSTICS?: string;
}

const TRUSTED_CACHE_ORIGIN = "https://cam-r2-video-cache.internal";
const INTERNAL_CACHE_CONTROL = "public, max-age=31536000, immutable";

function protectedHeaders(): Headers {
  return new Headers({
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
  });
}

function unauthorized(): Response {
  return new Response("Unavailable", { status: 403, headers: protectedHeaders() });
}

function unavailable(): Response {
  return new Response("Unavailable", { status: 503, headers: protectedHeaders() });
}

function maxTtl(raw?: string): number | null {
  if (raw === undefined) return 3600;
  if (!/^[1-9][0-9]{0,3}$/.test(raw)) return null;
  const parsed = Number(raw);
  return parsed <= 3600 ? parsed : null;
}

function validSecret(secret: unknown): secret is string {
  return typeof secret === "string" && encoderLength(secret) >= 32 &&
    new Set(secret).size >= 8 && !/[\x00-\x20\x7f]/.test(secret);
}

function encoderLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

type AuthRejectReason =
  | "invalid_request"
  | "invalid_expiry"
  | "invalid_secret"
  | "invalid_signature";

function authRejected(env: Env, reason: AuthRejectReason): null {
  if (env.R2_VIDEO_MEDIA_AUTH_DIAGNOSTICS === "true") {
    console.error(JSON.stringify({
      event: "video_cache_auth_rejected",
      reason,
    }));
  }
  return null;
}

function objectHeaders(object: R2ReadObject, cacheStatus: "HIT" | "MISS" | "BYPASS"): Headers {
  const headers = protectedHeaders();
  const contentType = object.httpMetadata?.contentType;
  headers.set("Content-Type", contentType?.startsWith("video/") ? contentType : "application/octet-stream");
  headers.set("Content-Length", String(object.size));
  headers.set("Accept-Ranges", "bytes");
  headers.set("X-Video-Cache", cacheStatus);
  if (object.httpEtag) headers.set("ETag", object.httpEtag);
  if (object.uploaded instanceof Date && Number.isFinite(object.uploaded.getTime())) {
    headers.set("Last-Modified", object.uploaded.toUTCString());
  }
  return headers;
}

function cacheRequest(pathname: string): Request {
  // A fixed trusted origin prevents Host from creating cache namespaces. The
  // strict, authenticated immutable pathname is the complete cache identity.
  return new Request(TRUSTED_CACHE_ORIGIN + pathname);
}

function cacheRepresentation(body: ReadableStream<Uint8Array>, object: R2ReadObject): Response {
  const headers = objectHeaders(object, "MISS");
  headers.set("Cache-Control", INTERNAL_CACHE_CONTROL);
  headers.delete("X-Video-Cache");
  return new Response(body, { status: 200, headers });
}

function scheduleCachePut(cache: EdgeCache | undefined, key: Request, response: Response, context?: ExecutionContext): void {
  if (!cache) return;
  const write = cache.put(key, response).catch(() => undefined);
  if (context) context.waitUntil(write);
  else void write;
}

async function authenticatedKey(request: Request, env: Env, nowSeconds: number): Promise<{ key: string; pathname: string } | null> {
  let url: URL;
  try {
    url = new URL(request.url);
  } catch {
    return null;
  }
  const key = cacheKeyFromPath(url.pathname);
  const parameters = url.searchParams;
  if (key === null || url.hash || parameters.size !== 3 ||
      parameters.getAll("v").length !== 1 || parameters.getAll("exp").length !== 1 ||
      parameters.getAll("sig").length !== 1 || parameters.get("v") !== "1") {
    return authRejected(env, "invalid_request");
  }
  const rawExpiry = parameters.get("exp");
  const signature = parameters.get("sig");
  if (rawExpiry === null || signature === null || !/^[1-9][0-9]{0,11}$/.test(rawExpiry)) {
    return authRejected(env, "invalid_expiry");
  }
  const expiresAt = Number(rawExpiry);
  const limit = maxTtl(env.R2_VIDEO_MEDIA_MAX_TTL_SECONDS);
  if (limit === null || !Number.isSafeInteger(nowSeconds) || !Number.isSafeInteger(expiresAt) ||
      expiresAt <= nowSeconds || expiresAt > nowSeconds + limit) {
    return authRejected(env, "invalid_expiry");
  }
  if (!validSecret(env.R2_VIDEO_MEDIA_SIGNING_SECRET)) {
    return authRejected(env, "invalid_secret");
  }
  if (!await verifyReadPath(env.R2_VIDEO_MEDIA_SIGNING_SECRET, url.pathname, expiresAt, signature)) {
    return authRejected(env, "invalid_signature");
  }
  return { key, pathname: url.pathname };
}

/** Exported for local fake-binding tests; default fetch uses the real clock. */
export async function handleRequest(
  request: Request, env: Env, nowSeconds = Math.floor(Date.now() / 1000),
  edgeCache?: EdgeCache, context?: ExecutionContext,
): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    const headers = protectedHeaders();
    headers.set("Allow", "GET, HEAD");
    return new Response("Method not allowed", { status: 405, headers });
  }

  const authenticated = await authenticatedKey(request, env, nowSeconds);
  if (authenticated === null) return unauthorized();

  try {
    if (request.method === "HEAD") {
      const object = await env.VIDEO_CACHE_BUCKET.head(authenticated.key);
      if (object === null) return new Response("Not found", { status: 404, headers: protectedHeaders() });
      if (!Number.isSafeInteger(object.size) || object.size < 0) return unavailable();
      return new Response(null, { status: 200, headers: objectHeaders(object, "BYPASS") });
    }

    if (!request.headers.has("Range")) {
      const canonical = cacheRequest(authenticated.pathname);
      const hit = edgeCache ? await edgeCache.match(canonical) : undefined;
      if (hit) {
        const headers = new Headers(hit.headers);
        headers.set("Cache-Control", "private, no-store");
        headers.set("X-Content-Type-Options", "nosniff");
        headers.set("Referrer-Policy", "no-referrer");
        headers.set("Accept-Ranges", "bytes");
        headers.set("X-Video-Cache", "HIT");
        return new Response(hit.body, { status: 200, headers });
      }
      const object = await env.VIDEO_CACHE_BUCKET.get(authenticated.key);
      if (object === null) return new Response("Not found", { status: 404, headers: protectedHeaders() });
      if (!Number.isSafeInteger(object.size) || object.size < 0 || object.body === undefined) return unavailable();
      const cacheable = cacheRepresentation(object.body, object);
      if (edgeCache) scheduleCachePut(edgeCache, canonical, cacheable.clone(), context);
      return new Response(cacheable.body, { status: 200, headers: objectHeaders(object, "MISS") });
    }

    const metadata = await env.VIDEO_CACHE_BUCKET.head(authenticated.key);
    if (metadata === null) return new Response("Not found", { status: 404, headers: protectedHeaders() });
    if (!Number.isSafeInteger(metadata.size) || metadata.size < 0) return unavailable();
    const parsed = parseSingleByteRange(request.headers.get("Range"), metadata.size);
    if (parsed.kind !== "valid") {
      const headers = protectedHeaders();
      headers.set("Content-Range", "bytes */" + metadata.size);
      headers.set("Accept-Ranges", "bytes");
      return new Response("Range not satisfiable", { status: 416, headers });
    }
    const object = await env.VIDEO_CACHE_BUCKET.get(authenticated.key, {
      range: { offset: parsed.start, length: parsed.length },
    });
    if (object === null || object.body === undefined) return unavailable();
    const headers = objectHeaders({ ...metadata, size: parsed.length }, "BYPASS");
    headers.set("Content-Range", "bytes " + parsed.start + "-" + parsed.end + "/" + metadata.size);
    return new Response(object.body, { status: 206, headers });
  } catch {
    return unavailable();
  }
}

export default {
  fetch(request: Request, env: Env, context: ExecutionContext): Promise<Response> {
    return handleRequest(request, env, Math.floor(Date.now() / 1000), (caches as unknown as { default: EdgeCache }).default, context);
  },
};

