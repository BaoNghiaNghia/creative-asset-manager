import test from "node:test";
import assert from "node:assert/strict";
import {createHmac, createHash} from "node:crypto";
import worker from "./index.mjs";

const secret = "12".repeat(32);
const key = "v1/" + "a".repeat(24) + "/sp-" + "b".repeat(40) +
  "/01/preview/" + "c".repeat(64) + "-w720v1.webp";
const path = "/v1/media/" + key;
const base = "https://example.workers.dev";
const exp = () => Math.floor(Date.now()/1000)+40;
const sign = (method, expiry, sha) => createHmac("sha256",Buffer.from(secret,"hex"))
  .update(method+"\n"+path+"\n"+expiry+(sha?"\n"+sha:"")).digest("hex");
const mock = new Map();
const bucket = {
  async head(path) {
    const bytes = mock.get(path);
    if (!bytes) return null;
    const etag = createHash("sha256").update(bytes).digest("hex");
    return {size:bytes.byteLength,etag,httpEtag:'"'+etag+'"'};
  },
  async get(path) {
    const metadata = await this.head(path);
    if (!metadata) return null;
    return {...metadata,body:mock.get(path)};
  },
  async put(path,data) {
    mock.set(path,Buffer.from(data));
  },
};
const env = {CAM_IMAGES:bucket,CAM_HMAC_HEX:secret};
const urlFor = (method, expiry=exp()) => base+path+
  "?exp="+expiry+"&sig="+sign(method,expiry);
const call = async (method,url,headers,body) =>
  worker.fetch(new Request(url,{method,headers,body}),env);

test("private Worker rejects unsigned, expired, tampered, and original URLs",async()=>{
  assert.equal((await call("GET",base+path)).status,403);
  assert.equal((await call("HEAD",base+path)).status,403);
  const past=Math.floor(Date.now()/1000)-2;
  assert.equal((await call("GET",urlFor("GET",past))).status,403);
  assert.equal((await call("GET",urlFor("GET").replace("a".repeat(24),"f".repeat(24)))).status,403);
  assert.equal((await call("GET",urlFor("GET")+"&extra=1")).status,403);
  assert.equal((await call("GET",base+path.replace("/preview/","/original/"))).status,404);
  assert.equal((await call("GET",urlFor("GET"))).status,404);
});
test("signed derivative PUT with verified body can be read by expiring GET/HEAD tickets",async()=>{
  const payload=Buffer.from("private-webp-payload");
  const sha=createHash("sha256").update(payload).digest("hex");
  const expiry=exp();
  const headers={"X-CAM-Expiry":String(expiry),"X-CAM-Digest":sha,
    "X-CAM-Signature":sign("PUT",expiry,sha),
    "Content-Type":"image/webp","Content-Length":String(payload.length)};
  let response=await call("PUT",base+path,{...headers,"X-CAM-Signature":"0".repeat(64)},payload);
  assert.equal(response.status,403);
  response=await call("PUT",base+path,{...headers,"X-CAM-Digest":"0".repeat(64)},payload);
  assert.equal(response.status,403);
  response=await call("PUT",base+path,headers,payload);
  assert.equal(response.status,204);
  response=await call("HEAD",urlFor("HEAD"));
  assert.equal(response.status,200);
  assert.equal(response.headers.get("content-type"),"image/webp");
  assert.equal(response.headers.get("cache-control"),"private, max-age=30, must-revalidate");
  response=await call("GET",urlFor("GET"));
  assert.equal(response.status,200);
  assert.equal(Buffer.from(await response.arrayBuffer()).toString(),payload.toString());
  assert.equal((await call("GET",base+path)).status,403);
});
test("disabled credentials and unsupported operations fail closed",async()=>{
  assert.equal((await worker.fetch(new Request(urlFor("GET")),
    {CAM_IMAGES:bucket,CAM_HMAC_HEX:""})).status,503);
  assert.equal((await call("DELETE",base+path)).status,404);
});
