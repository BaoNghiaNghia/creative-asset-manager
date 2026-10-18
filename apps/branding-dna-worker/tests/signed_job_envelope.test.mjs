import test from "node:test";
import assert from "node:assert/strict";
import {generateKeyPairSync, sign} from "node:crypto";
import {randomUUID} from "node:crypto";
import {verifyPreflightEnvelope, envelopeSigningBytes} from "../src/signed_job_envelope.mjs";

function sample(now=Math.floor(Date.now()/1000)){
  return {version:1,purpose:"preflight_only",scan_enabled:false,key_id:"test-v1",
    tenant_id:randomUUID(),shop_id:randomUUID(),task_id:randomUUID(),
    snapshot_id:randomUUID(),agent_id:randomUUID(),lease_id:randomUUID(),
    lease_generation:1,platform:"amazon",scope_sha256:"a".repeat(64),
    nonce:randomUUID(),issued_at:now,expires_at:now+90,max_pages:5,
    max_gallery_items:20};
}
function signed(claims){
  const {publicKey,privateKey}=generateKeyPairSync("ed25519");
  const signature=sign(null,envelopeSigningBytes(claims),privateKey).toString("base64url");
  return {packet:{...claims,signature},keys:{"test-v1":publicKey.export({format:"pem",type:"spki"})}};
}
function identity(packet){
  return {tenant_id:packet.tenant_id,shop_id:packet.shop_id,
    task_id:packet.task_id,snapshot_id:packet.snapshot_id,
    agent_id:packet.agent_id,lease_id:packet.lease_id,
    lease_generation:packet.lease_generation,platform:packet.platform,
    scope_sha256:packet.scope_sha256};
}
test("framing uses real line-feed bytes, not literal backslash-n",()=>{
  const value=envelopeSigningBytes(sample());
  assert.equal(value.subarray(0,26).toString(),"branding-dna-preflight-v1"+String.fromCharCode(10));
});
test("accepts valid signed bounded preview but not executable jobs",()=>{
  const claims=sample(), {packet,keys}=signed(claims);
  assert.deepEqual(verifyPreflightEnvelope(packet,{keys,expected:identity(packet),now:claims.issued_at}),claims);
});
test("rejects tamper, wrong scope, wrong worker, expired and unknown keys",()=>{
  const claims=sample(), {packet,keys}=signed(claims), expected=identity(packet);
  const cases=[
    [{...packet,max_pages:1},expected,claims.issued_at,keys],
    [{...packet,scan_enabled:true},expected,claims.issued_at,keys],
    [{...packet,purpose:"execute"},expected,claims.issued_at,keys],
    [packet,{...expected,agent_id:randomUUID()},claims.issued_at,keys],
    [packet,{...expected,shop_id:randomUUID()},claims.issued_at,keys],
    [packet,expected,claims.expires_at,keys],
    [packet,expected,claims.issued_at,{}],
    [{...packet,extra:"unexpected"},expected,claims.issued_at,keys],
  ];
  for(const [data,id,now,registry] of cases)
    assert.throws(()=>verifyPreflightEnvelope(data,{keys:registry,expected:id,now}));
});
test("refuses signature of different key and future issuance",()=>{
  const claims=sample(), signedPacket=signed(claims), other=signed(sample());
  assert.throws(()=>verifyPreflightEnvelope(signedPacket.packet,{
    keys:other.keys,expected:identity(signedPacket.packet),now:claims.issued_at}));
  assert.throws(()=>verifyPreflightEnvelope(signedPacket.packet,{
    keys:signedPacket.keys,expected:identity(signedPacket.packet),now:claims.issued_at-60}));
});
