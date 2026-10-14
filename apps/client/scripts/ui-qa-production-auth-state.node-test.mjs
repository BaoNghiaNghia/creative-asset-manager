import assert from "node:assert/strict";
import test from "node:test";
import { inspectProductionQaStorageState } from "./production-ui-auth-state.mjs";

const url = "https://creative-assets.ddns.net";
const now = 1791988000;
const cookie = (domain, expires) => ({
  name:"session", value:"test-secret-that-must-never-be-returned",
  domain, path:"/", expires, httpOnly:true, secure:true, sameSite:"Lax"
});

test("production session preflight accepts scoped, unexpired state without returning secret", () => {
  const result = inspectProductionQaStorageState({
    cookies:[cookie(".creative-assets.ddns.net", now+7200)],
    origins:[],
  }, url, now);
  assert.equal(result.ready,true);
  assert.equal(result.siteCookies,1);
  assert.ok(!JSON.stringify(result).includes("test-secret"));
});

test("production session preflight rejects foreign and expired cookies", () => {
  const foreign = {cookies:[cookie(".other.example",now+900)],origins:[]};
  assert.equal(inspectProductionQaStorageState(foreign,url,now).reason,
    "no-production-origin-session");
  const expired = {cookies:[cookie("creative-assets.ddns.net",now-900)],origins:[]};
  assert.equal(inspectProductionQaStorageState(expired,url,now).reason,
    "production-auth-cookies-expired");
  assert.equal(inspectProductionQaStorageState({cookies:[],origins:[]},url,now).ready,false);
  assert.equal(inspectProductionQaStorageState({cookies:[cookie("creative-assets.ddns.net",now+90)]},url,now).ready,false);
});

test("production session preflight accepts same-origin localStorage and rejects foreign origins", () => {
  const origins = [{origin:url,localStorage:[{name:"auth",value:"private-token"}]}];
  assert.equal(inspectProductionQaStorageState({cookies:[],origins},url,now).ready,true);
  assert.equal(inspectProductionQaStorageState({cookies:[],
    origins:[{origin:"https://other.example",localStorage:[{name:"auth",value:"x"}]}]},url,now).ready,false);
});
