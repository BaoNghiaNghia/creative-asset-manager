/** Private Etsy Spy derivative images on the EXISTING read-only R2 video Worker.
 *
 * Strictly disjoint from /video-cache/. The server uploads verified preview
 * bytes with its already provisioned S3 credential. Workers never write to R2.
 * A browser gets a ticket only after CAM checks principal/tenant/Picked ACL.
 * The ticket uses a domain-separated HMAC with the existing video secret.
 */
export interface ImageBucketObject {
  size: number;
  body?: ReadableStream<Uint8Array>;
  httpEtag?: string;
  httpMetadata?: { contentType?: string };
}
export interface ImageReadOnlyBucket {
  get(key: string): Promise<ImageBucketObject | null>;
  head(key: string): Promise<ImageBucketObject | null>;
}
export interface ImageEdgeCache {
  match(request: Request): Promise<Response | undefined>;
  put(request: Request, response: Response): Promise<void>;
}
export interface ImageContext {
  waitUntil(promise: Promise<unknown>): void;
}
export interface ImageEnv {
  VIDEO_CACHE_BUCKET: ImageReadOnlyBucket;
  R2_VIDEO_MEDIA_SIGNING_SECRET: string;
}

const IMAGE_PATH =
  /^\/etsy-images\/v1\/[0-9a-f]{24}\/sp-[0-9a-f]{40}\/(?:0[1-9]|10)\/(?:thumbnail\/[0-9a-f]{64}\.png|preview\/[0-9a-f]{64}-w720v1\.webp)$/;
const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
const MAX_IMAGE_TICKET_SECONDS = 60;
const INTERNAL_ORIGIN = "https://cam-r2-etsy-image-cache.internal";
const INTERNAL_CACHE_CONTROL = "public, max-age=31536000, immutable";
const textEncoder = new TextEncoder();
const SIG_PATTERN = /^[A-Za-z0-9_-]{43}$/;

function protectedHeaders(): Headers {
  return new Headers({
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  });
}
function fail(status: number): Response {
  return new Response("Unavailable", { status, headers: protectedHeaders() });
}
function mimeForPath(pathname: string): string | null {
  if (!IMAGE_PATH.test(pathname)) return null;
  return pathname.endsWith(".webp") ? "image/webp" : "image/png";
}
function encodeUrl64(bytes: Uint8Array): string {
  let raw = "";
  for (const b of bytes) raw += String.fromCharCode(b);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
function decodeUrl64(signature: string): Uint8Array<ArrayBuffer> | null {
  if (!SIG_PATTERN.test(signature)) return null;
  try {
    const raw = atob(signature.replace(/-/g, "+").replace(/_/g, "/") + "=");
    const decoded = Uint8Array.from(raw, c => c.charCodeAt(0));
    return decoded.length === 32 && encodeUrl64(decoded) === signature ? decoded : null;
  } catch {
    return null;
  }
}
export function canonicalEtsyImageMessage(path: string, exp: number): string {
  if (!IMAGE_PATH.test(path) || !Number.isSafeInteger(exp) || exp <= 0) {
    throw new TypeError("Invalid Etsy image ticket");
  }
  return `v1\netsy-image-read\n${path}\n${exp}`;
}
export async function signEtsyImagePath(secret: string, path: string, exp: number): Promise<string> {
  const key = await crypto.subtle.importKey("raw", textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key,
    textEncoder.encode(canonicalEtsyImageMessage(path, exp)));
  return encodeUrl64(new Uint8Array(sig));
}
async function authorized(request: Request, secret: string, now: number): Promise<string | null> {
  const url = new URL(request.url);
  if (!mimeForPath(url.pathname) || url.hash || url.searchParams.size !== 3 ||
      url.searchParams.getAll("v").length !== 1 ||
      url.searchParams.getAll("exp").length !== 1 ||
      url.searchParams.getAll("sig").length !== 1 ||
      url.searchParams.get("v") !== "1") return null;
  const rawExp = url.searchParams.get("exp") || "";
  const rawSig = url.searchParams.get("sig") || "";
  if (!/^[1-9][0-9]{0,11}$/.test(rawExp) || !SIG_PATTERN.test(rawSig) ||
      typeof secret !== "string" || textEncoder.encode(secret).length < 32 ||
      new Set(secret).size < 8 || /[\x00-\x20\x7f]/.test(secret)) return null;
  const exp = Number(rawExp);
  if (!Number.isSafeInteger(now) || !Number.isSafeInteger(exp) ||
      exp <= now || exp > now + MAX_IMAGE_TICKET_SECONDS) return null;
  const sig = decodeUrl64(rawSig);
  if (!sig) return null;
  try {
    const key = await crypto.subtle.importKey("raw", textEncoder.encode(secret),
      { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    const good = await crypto.subtle.verify("HMAC", key, sig,
      textEncoder.encode(canonicalEtsyImageMessage(url.pathname, exp)));
    return good ? url.pathname : null;
  } catch {
    return null;
  }
}
function bytesOkay(object: ImageBucketObject | null, expectedMime: string): object is ImageBucketObject {
  return object !== null && Number.isSafeInteger(object.size) &&
    object.size > 0 && object.size <= MAX_IMAGE_BYTES &&
    object.httpMetadata?.contentType === expectedMime;
}
function imageHeaders(object: ImageBucketObject, mime: string, status: "HIT" | "MISS" | "BYPASS"): Headers {
  const headers = protectedHeaders();
  headers.set("Content-Type", mime);
  headers.set("Content-Length", String(object.size));
  headers.set("X-CAM-Image-Cache", status);
  if (object.httpEtag) headers.set("ETag", object.httpEtag);
  return headers;
}
function toInternalRequest(pathname: string): Request {
  return new Request(INTERNAL_ORIGIN + pathname);
}
export async function handleEtsyImageRequest(
  request: Request, env: ImageEnv, now = Math.floor(Date.now() / 1000),
  cache?: ImageEdgeCache, context?: ImageContext,
): Promise<Response> {
  if (!["GET", "HEAD"].includes(request.method)) {
    const headers = protectedHeaders(); headers.set("Allow", "GET, HEAD");
    return new Response("Method not allowed", { status: 405, headers });
  }
  const path = await authorized(request, env.R2_VIDEO_MEDIA_SIGNING_SECRET, now);
  if (!path) return fail(403);
  const mime = mimeForPath(path);
  if (!mime) return fail(403);
  const key = path.slice(1);
  try {
    if (request.method === "HEAD") {
      const metadata = await env.VIDEO_CACHE_BUCKET.head(key);
      if (!metadata) return fail(404);
      if (!bytesOkay(metadata, mime)) return fail(503);
      return new Response(null, { status: 200, headers: imageHeaders(metadata, mime, "BYPASS") });
    }
    if (request.headers.has("Range")) return fail(416);
    const internal = toInternalRequest(path);
    const hit = cache ? await cache.match(internal) : undefined;
    if (hit && hit.status === 200 && hit.headers.get("Content-Type") === mime) {
      const headers = new Headers(hit.headers);
      headers.set("Cache-Control", "private, no-store");
      headers.set("Referrer-Policy", "no-referrer");
      headers.set("X-Content-Type-Options", "nosniff");
      headers.set("X-CAM-Image-Cache", "HIT");
      return new Response(hit.body, { status: 200, headers });
    }
    const object = await env.VIDEO_CACHE_BUCKET.get(key);
    if (!object) return fail(404);
    if (!bytesOkay(object, mime) || !object.body) return fail(503);
    if (cache) {
      const cacheHeaders = imageHeaders(object, mime, "MISS");
      cacheHeaders.set("Cache-Control", INTERNAL_CACHE_CONTROL);
      cacheHeaders.delete("X-CAM-Image-Cache");
      const cached = new Response(object.body, { status: 200, headers: cacheHeaders });
      const promise = cache.put(internal, cached.clone()).catch(() => undefined);
      if (context) context.waitUntil(promise); else void promise;
      return new Response(cached.body, { status: 200, headers: imageHeaders(object, mime, "MISS") });
    }
    return new Response(object.body, { status: 200, headers: imageHeaders(object, mime, "MISS") });
  } catch {
    return fail(503);
  }
}
