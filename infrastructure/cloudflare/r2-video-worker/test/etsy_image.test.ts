import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";
import { handleRequest, type EdgeCache, type Env } from "../src/index";
import { signEtsyImagePath } from "../src/etsy_image";

const NOW = 1794960000;
const SECRET = "v1-secure-Image-Worker-secret-" + "Ab3Dxy" .repeat(12);
const TENANT = "a".repeat(24);
const PACK = "sp-" + "b".repeat(40);
const SHA = "c".repeat(64);
const PNG_PATH = "/etsy-images/v1/" + TENANT + "/" + PACK +
  "/01/thumbnail/" + SHA + ".png";
const WEBP_PATH = "/etsy-images/v1/" + TENANT + "/" + PACK +
  "/02/preview/" + SHA + "-w720v1.webp";
const DATA = new TextEncoder().encode("private-image-derivative");
class Bucket {
  reads = 0;
  heads = 0;
  writes = 0;
  key = "";
  found = true;
  mime = "image/png";
  size = DATA.byteLength;
  get = async (key: string) => {
    this.reads++;this.key=key;
    return this.found ? { size:this.size, body:new Blob([DATA]).stream() as ReadableStream<Uint8Array>,
      httpMetadata:{ contentType:this.mime }, httpEtag:'"etag-image"' } : null;
  };
  head = async (key: string) => {
    this.heads++;this.key=key;
    return this.found ? {size:this.size,httpMetadata:{contentType:this.mime},httpEtag:'"etag-image"'} : null;
  };
  put = async () => {this.writes++;throw new Error("image CDN must be read-only");};
}
class Cache implements EdgeCache {
  values = new Map<string, Response>();
  puts = 0;
  async match(r: Request) {return this.values.get(r.url)?.clone();}
  async put(r: Request, response: Response) {this.puts++;this.values.set(r.url,response.clone());}
}
const fixture = () => {
  const bucket = new Bucket();
  return { bucket, env:{VIDEO_CACHE_BUCKET:bucket,R2_VIDEO_MEDIA_SIGNING_SECRET:SECRET,
    R2_VIDEO_MEDIA_MAX_TTL_SECONDS:"3600"} as Env };
};
async function url(path=PNG_PATH, exp=NOW+45, secret=SECRET) {
  const sig=await signEtsyImagePath(secret,path,exp);
  return "https://media.example.test"+path+"?v=1&exp="+exp+"&sig="+sig;
}
const send = (requestUrl: string, env:Env, now=NOW, method="GET",cache?:EdgeCache) =>
  handleRequest(new Request(requestUrl,{method}),env,now,cache);

test("private PNG route signs correct domain-separated canonical message", async()=>{
  const signature=await signEtsyImagePath(SECRET,PNG_PATH,NOW+45);
  const expected=createHmac("sha256",SECRET)
    .update("v1\netsy-image-read\n"+PNG_PATH+"\n"+String(NOW+45))
    .digest("base64url");
  assert.equal(signature,expected);
  const {bucket,env}=fixture();
  const response=await send(await url(),env);
  assert.equal(response.status,200);
  assert.equal(response.headers.get("Content-Type"),"image/png");
  assert.equal(response.headers.get("Cache-Control"),"private, no-store");
  assert.equal(response.headers.get("Referrer-Policy"),"no-referrer");
  assert.equal(response.headers.get("Access-Control-Allow-Origin"),null);
  assert.equal(response.headers.get("X-CAM-Image-Cache"),"MISS");
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()),DATA);
  assert.equal(bucket.key,PNG_PATH.slice(1));
  assert.equal(bucket.writes,0);
});
test("preview WebP route uses same read-only bucket, HEAD metadata only",async()=>{
  const {bucket,env}=fixture();bucket.mime="image/webp";
  const read=await url(WEBP_PATH);
  const head=await send(read,env,NOW,"HEAD");
  assert.equal(head.status,200);
  assert.equal(head.body,null);
  assert.equal(head.headers.get("Content-Type"),"image/webp");
  assert.deepEqual([bucket.heads,bucket.reads,bucket.writes],[1,0,0]);
  assert.equal((await send(read,env)).status,200);
});
test("missing object gives 404 only for valid signature",async()=>{
  const {bucket,env}=fixture();bucket.found=false;
  assert.equal((await send(await url(),env)).status,404);
  assert.equal((await send("https://media.example.test"+PNG_PATH,env)).status,403);
  assert.equal(bucket.reads,1);
});
test("reject unsigned, wrong tenant, expired, overlong, original, malformed, video-ticket",async()=>{
  const {bucket,env}=fixture();
  assert.equal((await send("https://media.example.test"+PNG_PATH,env)).status,403);
  assert.equal((await send(await url(PNG_PATH,NOW-1),env)).status,403);
  assert.equal((await send(await url(PNG_PATH,NOW+61),env)).status,403);
  assert.equal((await send(await url(PNG_PATH,NOW+45,"Different-Secure-Secret-Ab3Dxyz"),
    env)).status,403);
  assert.equal((await send((await url()).replace("/"+TENANT+"/","/"+"f".repeat(24)+"/"),env)).status,403);
  assert.equal((await send((await url())+"&extra=1",env)).status,403);
  assert.equal((await send("https://media.example.test"+PNG_PATH.replace("/thumbnail/","/original/"),
    env)).status,403);
  assert.equal((await send((await url()).replace("/01/","/11/"),env)).status,403);
  const badSig=createHmac("sha256",SECRET)
    .update("v1\nread\n"+PNG_PATH+"\n"+String(NOW+45))
    .digest("base64url");
  assert.equal((await send("https://media.example.test"+PNG_PATH+
    "?v=1&exp="+(NOW+45)+"&sig="+badSig,env)).status,403);
  assert.equal(bucket.reads,0);
});
test("immutable image bytes are cached only after signed verification",async()=>{
  const {bucket,env}=fixture();
  const cache=new Cache();
  const t=await url();
  const first=await send(t,env,NOW,"GET",cache);
  assert.equal(first.status,200);
  assert.equal(first.headers.get("X-CAM-Image-Cache"),"MISS");
  await first.arrayBuffer();
  await new Promise(resolve=>setTimeout(resolve,0));
  const second=await send(t,env,NOW,"GET",cache);
  assert.equal(second.status,200);
  assert.equal(second.headers.get("X-CAM-Image-Cache"),"HIT");
  assert.equal(second.headers.get("Cache-Control"),"private, no-store");
  assert.equal(bucket.reads,1);
  assert.equal(cache.puts,1);
  assert.equal(cache.values.size,1);
  assert.equal((await send(t,env,NOW+46,"GET",cache)).status,403);
  assert.equal(bucket.reads,1);
});
test("oversize, wrong MIME and range fail closed, video unaffected",async()=>{
  const {bucket,env}=fixture();
  bucket.size=2*1024*1024+1;
  assert.equal((await send(await url(),env)).status,503);
  bucket.size=DATA.byteLength;bucket.mime="video/mp4";
  assert.equal((await send(await url(),env)).status,503);
  bucket.mime="image/png";
  assert.equal((await handleRequest(new Request(await url(),{
    headers:{"Range":"bytes=0-3"}}),env,NOW)).status,416);
  assert.equal((await send(await url(),env,NOW,"PUT")).status,405);
  assert.equal(bucket.writes,0);
});
