import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { handleRequest, type EdgeCache, type Env, type R2ReadObject } from "../src/index";
import { signReadPath } from "../src/signature";

const vector = JSON.parse(readFileSync(new URL("./vectors.json", import.meta.url), "utf8")) as {
  secret: string; pathname: string; expires_at: number; signature: string;
};
const NOW = vector.expires_at - 300;
const BYTES = new TextEncoder().encode("original-video-bytes");

class FakeBucket {
  getCalls = 0;
  headCalls = 0;
  listCalls = 0;
  missing = false;
  lastKey = "";
  lastRange: { offset: number; length: number } | undefined;
  async get(key: string, options?: { range?: { offset: number; length: number } }): Promise<R2ReadObject | null> {
    this.getCalls++;
    this.lastKey = key;
    this.lastRange = options?.range;
    if (this.missing) return null;
    const bytes = options?.range ? BYTES.slice(options.range.offset, options.range.offset + options.range.length) : BYTES;
    return { size: bytes.byteLength, body: new Blob([bytes]).stream() as ReadableStream<Uint8Array>,
      httpEtag: '"etag-original"', uploaded: new Date("2026-01-01T00:00:00Z"),
      httpMetadata: { contentType: "video/mp4" } };
  }
  async head(key: string): Promise<R2ReadObject | null> {
    this.headCalls++;
    this.lastKey = key;
    if (this.missing) return null;
    return { size: BYTES.byteLength, httpEtag: '"etag-original"',
      uploaded: new Date("2026-01-01T00:00:00Z"), httpMetadata: { contentType: "video/mp4" } };
  }
  list(): never { this.listCalls++; throw new Error("listing forbidden"); }
}
class FakeCache implements EdgeCache {
  entries = new Map<string, Response>();
  matches: string[] = [];
  puts: string[] = [];
  async match(request: Request): Promise<Response | undefined> {
    this.matches.push(request.url);
    return this.entries.get(request.url)?.clone();
  }
  async put(request: Request, response: Response): Promise<void> {
    this.puts.push(request.url);
    this.entries.set(request.url, response.clone());
  }
}
function fixture() {
  const bucket = new FakeBucket();
  const env: Env = { VIDEO_CACHE_BUCKET: bucket, R2_VIDEO_MEDIA_SIGNING_SECRET: vector.secret,
    R2_VIDEO_MEDIA_MAX_TTL_SECONDS: "3600" };
  const url = `https://media.example.test${vector.pathname}?v=1&exp=${vector.expires_at}&sig=${vector.signature}`;
  return { bucket, env, url };
}
function send(url: string, env: Env, method = "GET", now = NOW, headers?: HeadersInit, edgeCache?: EdgeCache) {
  return handleRequest(new Request(url, { method, headers }), env, now, edgeCache);
}

test("valid GET streams exact bytes and conservative metadata", async () => {
  const { bucket, env, url } = fixture();
  const response = await send(url, env);
  assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), BYTES);
  for (const [name, expected] of [
    ["Content-Type", "video/mp4"], ["Content-Length", String(BYTES.byteLength)],
    ["ETag", '"etag-original"'], ["Last-Modified", "Thu, 01 Jan 2026 00:00:00 GMT"],
    ["Cache-Control", "private, no-store"], ["X-Content-Type-Options", "nosniff"],
    ["Referrer-Policy", "no-referrer"], ["Accept-Ranges", "bytes"], ["X-Video-Cache", "MISS"],
  ]) assert.equal(response.headers.get(name), expected);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), null);
  assert.equal(bucket.lastKey, vector.pathname.slice(1));
  assert.deepEqual([bucket.getCalls, bucket.headCalls, bucket.listCalls], [1, 0, 0]);
});

test("same read ticket authorizes HEAD but returns metadata only", async () => {
  const { bucket, env, url } = fixture();
  const response = await send(url, env, "HEAD");
  assert.equal(response.status, 200);
  assert.equal(response.body, null);
  assert.equal(response.headers.get("Content-Length"), String(BYTES.byteLength));
  assert.equal(response.headers.get("Content-Type"), "video/mp4");
  assert.deepEqual([bucket.getCalls, bucket.headCalls, bucket.listCalls], [0, 1, 0]);
});

test("unsigned missing object is generic denial; signed missing object is 404", async () => {
  const { bucket, env, url } = fixture();
  bucket.missing = true;
  assert.equal((await send(url.split("?")[0], env)).status, 403);
  assert.equal(bucket.getCalls, 0);
  assert.equal((await send(url, env)).status, 404);
  assert.equal(bucket.getCalls, 1);
});

test("PUT POST PATCH DELETE are 405 before R2 lookup", async () => {
  const { bucket, env, url } = fixture();
  for (const method of ["PUT", "POST", "PATCH", "DELETE"]) {
    const response = await send(url, env, method);
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("Allow"), "GET, HEAD");
  }
  assert.deepEqual([bucket.getCalls, bucket.headCalls, bucket.listCalls], [0, 0, 0]);
});

test("invalid, missing, duplicated or tampered query fields are generic denial", async () => {
  const { bucket, env, url } = fixture();
  for (const candidate of [
    url.replace(vector.signature, "A".repeat(43)), url.replace("v=1", "v=2"),
    url.replace(`exp=${vector.expires_at}`, "exp=NaN"),
    url.replace(`exp=${vector.expires_at}`, "exp=0001"),
    url.replace(`exp=${vector.expires_at}`, `exp=${vector.expires_at + 1}`),
    url.replace("&sig=", "&extra=1&sig="), url + "&sig=" + vector.signature,
    url.replace("&sig=" + vector.signature, ""), url.replace("v=1&", ""),
  ]) {
    const response = await send(candidate, env);
    assert.equal(response.status, 403, candidate);
    assert.equal(await response.text(), "Unavailable");
  }
  assert.equal(bucket.getCalls + bucket.headCalls, 0);
});

test("expired and correctly signed far-future tickets fail before lookup", async () => {
  const { bucket, env, url } = fixture();
  assert.equal((await send(url, env, "GET", vector.expires_at)).status, 403);
  const future = NOW + 3601;
  const sig = await signReadPath(vector.secret, vector.pathname, future);
  assert.equal((await send(`https://media.example.test${vector.pathname}?v=1&exp=${future}&sig=${sig}`, env)).status, 403);
  assert.equal(bucket.getCalls, 0);
});

test("path tampering, traversal, encoded separators and foreign prefix deny", async () => {
  const { bucket, env, url } = fixture();
  for (const path of [
    vector.pathname.replace("test-tenant", "another-tenant"),
    vector.pathname + ".mp4", vector.pathname + "%2fother", vector.pathname + "%5cother",
    vector.pathname.replace("test-tenant", "test%2ftenant"),
    vector.pathname.replace("test-tenant", "test%5ctenant"),
    vector.pathname.replace("test-tenant", "%2e%2e"),
    vector.pathname.replace("/video-cache/", "/other/"),
    vector.pathname.replace("/original", "//original"),
  ]) {
    const candidate = new URL(url);
    candidate.pathname = path;
    assert.equal((await send(candidate.toString(), env)).status, 403, path);
  }
  assert.equal(bucket.getCalls + bucket.headCalls, 0);
});

test("malformed secret and TTL fail closed", async () => {
  const { bucket, env, url } = fixture();
  for (const secret of ["", "short", "x".repeat(64)])
    assert.equal((await send(url, { ...env, R2_VIDEO_MEDIA_SIGNING_SECRET: secret })).status, 403);
  for (const ttl of ["0", "3601", "x"])
    assert.equal((await send(url, { ...env, R2_VIDEO_MEDIA_MAX_TTL_SECONDS: ttl })).status, 403);
  assert.equal(bucket.getCalls, 0);
});

test("auth diagnostics can identify signature rejection without exposing ticket material", async () => {
  const { env, url } = fixture();
  const candidate = url.replace(vector.signature, "A".repeat(43));
  const originalError = console.error;
  const logs: unknown[] = [];
  console.error = (...args: unknown[]) => logs.push(args);
  try {
    const response = await send(
      candidate,
      { ...env, R2_VIDEO_MEDIA_AUTH_DIAGNOSTICS: "true" },
    );
    assert.equal(response.status, 403);
  } finally {
    console.error = originalError;
  }
  assert.equal(logs.length, 1);
  const line = JSON.stringify(logs[0]);
  assert.match(line, /video_cache_auth_rejected/);
  assert.match(line, /invalid_signature/);
  assert.doesNotMatch(line, new RegExp(vector.secret));
  assert.doesNotMatch(line, /video-cache/);
  assert.doesNotMatch(line, /sig=/);
});


test("R2 failure is generic and never reflects provider error or secret", async () => {
  const { bucket, env, url } = fixture();
  bucket.get = async () => { throw new Error("provider-secret-must-not-leak"); };
  const response = await send(url, env);
  assert.equal(response.status, 503);
  assert.equal(await response.text(), "Unavailable");
  assert.equal(response.headers.get("Cache-Control"), "private, no-store");
  assert.equal(bucket.listCalls, 0);
});

test("non-video R2 content type is not reflected as active content", async () => {
  const { bucket, env, url } = fixture();
  bucket.head = async () => ({
    size: BYTES.byteLength, httpMetadata: { contentType: "text/html" },
  });
  const response = await send(url, env, "HEAD");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Content-Type"), "application/octet-stream");
  assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
});

test("single byte ranges return exact 206 bytes using native R2 partial reads", async () => {
  const { bucket, env, url } = fixture();
  for (const [header, expectedStart, expectedEnd] of [["bytes=0-3", 0, 3], ["bytes=4-", 4, BYTES.byteLength - 1], ["bytes=-4", BYTES.byteLength - 4, BYTES.byteLength - 1], ["bytes=1-999", 1, BYTES.byteLength - 1]] as const) {
    const response = await send(url, env, "GET", NOW, { Range: header });
    assert.equal(response.status, 206, header);
    assert.equal(response.headers.get("Content-Range"), "bytes " + expectedStart + "-" + expectedEnd + "/" + BYTES.byteLength);
    assert.equal(response.headers.get("Content-Length"), String(expectedEnd - expectedStart + 1));
    assert.equal(response.headers.get("Accept-Ranges"), "bytes");
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), BYTES.slice(expectedStart, expectedEnd + 1));
    assert.deepEqual(bucket.lastRange, { offset: expectedStart, length: expectedEnd - expectedStart + 1 });
  }
  assert.equal(bucket.headCalls, 4);
});

test("invalid, multi, zero suffix and unsatisfiable ranges return 416 and are not cached", async () => {
  const { bucket, env, url } = fixture();
  const cache = new FakeCache();
  for (const header of ["bytes=999-", "bytes=3-1", "bytes=-0", "bytes=0-1,4-5", "items=0-1", "bytes=nope"]) {
    const response = await send(url, env, "GET", NOW, { Range: header }, cache);
    assert.equal(response.status, 416, header);
    assert.equal(response.headers.get("Content-Range"), "bytes */" + BYTES.byteLength);
  }
  assert.equal(cache.puts.length, 0);
  assert.equal(bucket.getCalls, 0);
});

test("HEAD has metadata only and does not create a partial cache object", async () => {
  const { bucket, env, url } = fixture();
  const cache = new FakeCache();
  const response = await send(url, env, "HEAD", NOW, { Range: "bytes=0-2" }, cache);
  assert.equal(response.status, 200);
  assert.equal(response.body, null);
  assert.equal(response.headers.get("Content-Length"), String(BYTES.byteLength));
  assert.equal(cache.puts.length, 0);
  assert.equal(bucket.getCalls, 0);
});

test("authenticated full GETs share one server-side canonical cache key", async () => {
  const { bucket, env, url } = fixture();
  const cache = new FakeCache();
  const first = await send(url, env, "GET", NOW, undefined, cache);
  assert.equal(first.status, 200);
  await first.arrayBuffer();
  await new Promise(resolve => setTimeout(resolve, 0));
  const expiry = NOW + 301;
  const signature = await signReadPath(vector.secret, vector.pathname, expiry);
  const second = await send("https://untrusted-host.example" + vector.pathname + "?v=1&exp=" + expiry + "&sig=" + signature, env, "GET", NOW, undefined, cache);
  assert.equal(second.status, 200);
  assert.equal(second.headers.get("X-Video-Cache"), "HIT");
  assert.deepEqual(new Uint8Array(await second.arrayBuffer()), BYTES);
  assert.equal(bucket.getCalls, 1);
  assert.equal(cache.entries.size, 1);
  assert.equal(cache.matches[0], "https://cam-r2-video-cache.internal" + vector.pathname);
  assert.equal(cache.matches[1], "https://cam-r2-video-cache.internal" + vector.pathname);
  assert.equal(cache.matches.join("&").includes("sig="), false);
  assert.equal(cache.matches.join("&").includes("exp="), false);
});

test("cache is never looked up before ticket validation and failures are not cached", async () => {
  const { bucket, env, url } = fixture();
  const cache = new FakeCache();
  const unsigned = await send(url.split("?")[0], env, "GET", NOW, undefined, cache);
  assert.equal(unsigned.status, 403);
  const expired = await send(url, env, "GET", vector.expires_at, undefined, cache);
  assert.equal(expired.status, 403);
  const tampered = await send(url.replace("test-tenant", "other-tenant"), env, "GET", NOW, undefined, cache);
  assert.equal(tampered.status, 403);
  assert.equal(cache.matches.length, 0);
  assert.equal(cache.puts.length, 0);
  assert.equal(bucket.getCalls + bucket.headCalls, 0);
});

test("bounded overlapping ranges reuse one fixed edge segment", async () => {
  const { bucket, env, url } = fixture();
  const cache = new FakeCache();
  const first = await send(url, env, "GET", NOW, { Range: "bytes=1-3" }, cache);
  assert.equal(first.status, 206);
  assert.equal(first.headers.get("X-Video-Cache"), "MISS");
  assert.deepEqual(new Uint8Array(await first.arrayBuffer()), BYTES.slice(1, 4));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(cache.puts.length, 1);
  assert.equal(
    cache.puts[0],
    "https://cam-r2-video-cache.internal" + vector.pathname + "?segment=0-" + (BYTES.byteLength - 1),
  );
  assert.deepEqual(bucket.lastRange, { offset: 0, length: BYTES.byteLength });

  const second = await send(url, env, "GET", NOW, { Range: "bytes=2-5" }, cache);
  assert.equal(second.status, 206);
  assert.equal(second.headers.get("X-Video-Cache"), "HIT");
  assert.deepEqual(new Uint8Array(await second.arrayBuffer()), BYTES.slice(2, 6));
  assert.equal(bucket.getCalls, 1);
  assert.equal(cache.matches.length, 2);
});


test("404 and provider failures are not cached", async () => {
  const { bucket, env, url } = fixture();
  const cache = new FakeCache();
  bucket.missing = true;
  assert.equal((await send(url, env, "GET", NOW, undefined, cache)).status, 404);
  bucket.missing = false;
  bucket.get = async () => { throw new Error("provider failure"); };
  assert.equal((await send(url, env, "GET", NOW, undefined, cache)).status, 503);
  assert.equal(cache.puts.length, 0);
});
