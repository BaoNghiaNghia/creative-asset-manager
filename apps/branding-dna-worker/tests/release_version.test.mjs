import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {AGENT_VERSION} from "../src/agent_cam.mjs";
import {VERSION} from "../src/run_configured_pilot.mjs";
import {heartbeatOnce} from "../src/heartbeat.mjs";

test("pilot reports and both heartbeat protocols identify the shipped package version",async()=>{
 const expected=JSON.parse(readFileSync(new URL("../package.json",import.meta.url),"utf8")).version;
 assert.equal(VERSION,expected,"configured reports must identify the shipped runtime");
 assert.equal(AGENT_VERSION,expected,"paired CAM heartbeat must identify the shipped runtime");
 let body;
 await heartbeatOnce({url:"https://cam.example.test",
  agentId:"12345678-1234-1234-1234-123456789abc",token:"fixture-only"},
  async(_url,options)=>{
   body=JSON.parse(options.body);
   return {ok:true,status:200,json:async()=>({scan_enabled:false})};
  });
 assert.equal(body.agent_version,expected,"legacy worker heartbeat must identify the shipped runtime");
 assert.equal(body.status,"idle");
});
