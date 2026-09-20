import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { cacheKeyFromPath } from "../src/path";
import { canonicalReadMessage, signReadPath, verifyReadPath } from "../src/signature";

const vector = JSON.parse(readFileSync(new URL("./vectors.json", import.meta.url), "utf8")) as {
  secret: string; pathname: string; expires_at: number; canonical: string; signature: string;
};

test("fixed Python/Worker canonical and HMAC vector", async () => {
  assert.equal(canonicalReadMessage(vector.pathname, vector.expires_at), vector.canonical);
  assert.equal(await signReadPath(vector.secret, vector.pathname, vector.expires_at), vector.signature);
  assert.equal(await verifyReadPath(vector.secret, vector.pathname, vector.expires_at, vector.signature), true);
});

test("pathname, expiry, and secret are bound to the ticket", async () => {
  for (const [secret, path, exp] of [
    [vector.secret, vector.pathname.replace("test-tenant", "other-tenant"), vector.expires_at],
    [vector.secret, vector.pathname, vector.expires_at + 1],
    ["different-test-secret-with-entropy-2026", vector.pathname, vector.expires_at],
  ] as const) {
    assert.equal(await verifyReadPath(secret, path, exp, vector.signature), false);
  }
});

test("malformed and noncanonical signatures fail", async () => {
  for (const candidate of ["", "not-base64", "A".repeat(42), "A".repeat(44), "A".repeat(43)]) {
    assert.equal(await verifyReadPath(vector.secret, vector.pathname, vector.expires_at, candidate), false);
  }
});

test("only exact ASCII original-video cache paths map to R2", () => {
  assert.equal(cacheKeyFromPath(vector.pathname), vector.pathname.slice(1));
  for (const path of [
    "/video-cache", vector.pathname + "/", vector.pathname + ".mp4",
    vector.pathname.replace("/original", "//original"),
    vector.pathname.replace("test-tenant", ".."),
    vector.pathname + "%2Fother", vector.pathname + "%5cother",
    vector.pathname + "\\other", vector.pathname + "\n",
    vector.pathname.replace("/video-cache/", "/other-bucket/"),
    vector.pathname.replace("test-tenant", "test%2ftenant"),
  ]) {
    assert.equal(cacheKeyFromPath(path), null, path);
  }
});
