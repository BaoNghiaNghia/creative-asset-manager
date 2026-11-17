// CAM Etsy Spy private image delivery. Never expose the R2 bucket publicly.
// Requests must carry a short-lived HMAC from CAM AFTER its tenant/Picked ACL.
const PATH = /^\/v1\/media\/(v1\/[0-9a-f]{24}\/sp-[0-9a-f]{40}\/(?:0[1-9]|10)\/(thumbnail\/[0-9a-f]{64}\.png|preview\/[0-9a-f]{64}-w720v1\.webp))$/;
const MAX_BYTES = 2 * 1024 * 1024;
const MAX_FUTURE_SECONDS = 60;
const noStore = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer" };
const textEncoder = new TextEncoder();

function hexToBytes(value) {
  if (!/^[0-9a-f]{64}$/.test(value)) return null;
  return Uint8Array.from(value.match(/.{2}/g), pair => parseInt(pair, 16));
}

async function validMac(secretHex, message, received) {
  if (!/^[0-9a-fA-F]{64,128}$/.test(secretHex || "")) return false;
  const keyBytes = Uint8Array.from(secretHex.match(/.{2}/g), pair => parseInt(pair, 16));
  const signature = hexToBytes(received || "");
  if (!signature) return false;
  const key = await crypto.subtle.importKey("raw", keyBytes,
    { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  return crypto.subtle.verify("HMAC", key, signature, textEncoder.encode(message));
}

function validExpiry(value) {
  if (!/^\d{10}$/.test(value || "")) return false;
  const now = Math.floor(Date.now() / 1000);
  const expiry = Number(value);
  return Number.isSafeInteger(expiry) && expiry >= now && expiry <= now + MAX_FUTURE_SECONDS;
}

export default {
  async fetch(request, env) {
    if (!env?.CAM_IMAGES || !env?.CAM_HMAC_HEX) {
      return new Response("Unavailable", { status: 503, headers: noStore });
    }
    const url = new URL(request.url);
    const match = PATH.exec(url.pathname);
    if (!match || !["GET", "HEAD", "PUT"].includes(request.method)) {
      return new Response("Not found", { status: 404, headers: noStore });
    }
    const key = match[1];
    const type = key.endsWith(".webp") ? "image/webp" : "image/png";
    const now = request.method === "PUT" ? request.headers.get("X-CAM-Expiry")
      : url.searchParams.get("exp");
    if (!validExpiry(now)) {
      return new Response("Expired or invalid", { status: 403, headers: noStore });
    }
    if (request.method !== "PUT" && ([...url.searchParams.keys()].sort().join(",") !== "exp,sig")) {
      return new Response("Invalid ticket", { status: 403, headers: noStore });
    }
    const claimedDigest = request.method === "PUT"
      ? request.headers.get("X-CAM-Digest") : null;
    if (request.method === "PUT" && (!/^[0-9a-f]{64}$/.test(claimedDigest || "")
      || url.search !== "")) {
      return new Response("Invalid upload", { status: 403, headers: noStore });
    }
    const message = request.method + "\n" + url.pathname + "\n" + now +
      (request.method === "PUT" ? "\n" + claimedDigest : "");
    const received = request.method === "PUT"
      ? request.headers.get("X-CAM-Signature") : url.searchParams.get("sig");
    if (!await validMac(env.CAM_HMAC_HEX, message, received)) {
      return new Response("Invalid signature", { status: 403, headers: noStore });
    }
    if (request.method === "PUT") {
      const advertised = Number(request.headers.get("content-length"));
      if (!Number.isInteger(advertised) || advertised <= 0 || advertised > MAX_BYTES ||
          request.headers.get("content-type")?.split(";")[0] !== type) {
        return new Response("Invalid payload", { status: 413, headers: noStore });
      }
      const body = await request.arrayBuffer();
      if (body.byteLength !== advertised || body.byteLength > MAX_BYTES) {
        return new Response("Invalid payload", { status: 413, headers: noStore });
      }
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", body));
      const declared = hexToBytes(claimedDigest);
      let different = declared ? 0 : 1;
      for (let i = 0; i < 32; i++) different |= digest[i] ^ (declared?.[i] ?? 0);
      if (different !== 0) {
        return new Response("Digest mismatch", { status: 422, headers: noStore });
      }
      const prior = await env.CAM_IMAGES.head(key);
      if (!prior) {
        await env.CAM_IMAGES.put(key, body, { httpMetadata: { contentType: type } });
      }
      return new Response(null, { status: 204, headers: noStore });
    }
    const object = request.method === "HEAD"
      ? await env.CAM_IMAGES.head(key) : await env.CAM_IMAGES.get(key);
    if (!object) return new Response("Not found", { status: 404, headers: noStore });
    const headers = new Headers({
      "Content-Type": type,
      "Cache-Control": "private, max-age=30, must-revalidate",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "ETag": object.httpEtag || ('"' + object.etag + '"'),
    });
    if (object.size !== undefined) headers.set("Content-Length", String(object.size));
    return new Response(request.method === "HEAD" ? null : object.body,
      { status: 200, headers });
  },
};
