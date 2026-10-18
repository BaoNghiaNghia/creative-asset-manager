/** Offline, NON-EXECUTABLE Ed25519 preflight envelope verification.
 * The worker must receive an independently pinned public key and expected
 * tenant/shop/task/agent/lease/scope from a trusted caller. Never use claims
 * from the packet as their own expected values in an executable path.
 * No live worker imports this module; /lease remains disabled.
 */
import {createPublicKey, verify} from "node:crypto";

const FIELDS=["agent_id","expires_at","issued_at","key_id","lease_generation",
 "lease_id","max_gallery_items","max_pages","nonce","platform","purpose",
 "scan_enabled","scope_sha256","shop_id","snapshot_id","task_id",
 "tenant_id","version"];
const IDS=["tenant_id","shop_id","task_id","snapshot_id","agent_id","lease_id","nonce"];
const EXPECTED=["tenant_id","shop_id","task_id","snapshot_id","agent_id",
 "lease_id","lease_generation","platform","scope_sha256"];
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH=/^[0-9a-f]{64}$/;
const KEY=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const B64URL=/^[A-Za-z0-9_-]{86}$/;
const has=(o,k)=>Object.prototype.hasOwnProperty.call(o,k);
const plain=o=>o!==null && typeof o==="object" && !Array.isArray(o) &&
 (Object.getPrototypeOf(o)===Object.prototype || Object.getPrototypeOf(o)===null);
const integer=(n,min,max)=>Number.isSafeInteger(n)&&n>=min&&n<=max;

export function validatePreflightClaims(claims,{now}){
 if(!plain(claims)||Object.keys(claims).sort().join("|")!==FIELDS.slice().sort().join("|"))
   throw Error("invalid_envelope_fields");
 if(!integer(now,1,Number.MAX_SAFE_INTEGER))throw Error("invalid_current_time");
 if(claims.version!==1||claims.purpose!=="preflight_only"||claims.scan_enabled!==false)
   throw Error("envelope_cannot_enable_scan");
 if(!["amazon","etsy"].includes(claims.platform))throw Error("invalid_platform");
 if(typeof claims.key_id!=="string"||!KEY.test(claims.key_id))throw Error("invalid_key_id");
 if(typeof claims.scope_sha256!=="string"||!HASH.test(claims.scope_sha256))
   throw Error("invalid_scope_digest");
 for(const key of IDS)
   if(typeof claims[key]!=="string"||!UUID.test(claims[key]))throw Error("invalid_envelope_identity");
 if(!integer(claims.lease_generation,1,1000000)||
    !integer(claims.max_pages,1,5)||!integer(claims.max_gallery_items,1,20))
   throw Error("invalid_envelope_budget");
 if(!integer(claims.issued_at,1,Number.MAX_SAFE_INTEGER)||
    !integer(claims.expires_at,1,Number.MAX_SAFE_INTEGER)||
    claims.issued_at<now-300||claims.issued_at>now+30||
    claims.expires_at<=now||claims.expires_at<=claims.issued_at||
    claims.expires_at>claims.issued_at+300)
   throw Error("envelope_expired_or_outside_window");
}
export function envelopeSigningBytes(claims){
 // Exact ASCII-safe fields; validated before cryptographic operations.
 return Buffer.from("branding-dna-preflight-v1\n"+
   FIELDS.map(k=>k+"="+(k==="scan_enabled"?"false":String(claims[k]))+"\n").join(""),"ascii");
}
export function verifyPreflightEnvelope(packet,{keys,expected,now}={}){
 if(!plain(packet)||!plain(keys)||!plain(expected))throw Error("invalid_envelope_context");
 if(Object.keys(packet).length!==FIELDS.length+1||!has(packet,"signature"))
   throw Error("invalid_signed_envelope_fields");
 const {signature,...claims}=packet;
 validatePreflightClaims(claims,{now});
 for(const key of EXPECTED)
   if(!has(expected,key)||expected[key]!==claims[key])throw Error("envelope_identity_mismatch");
 if(typeof signature!=="string"||!B64URL.test(signature))throw Error("invalid_envelope_signature");
 if(!has(keys,claims.key_id)||typeof keys[claims.key_id]!=="string")
   throw Error("unknown_envelope_public_key");
 const sig=Buffer.from(signature,"base64url");
 if(sig.length!==64||sig.toString("base64url")!==signature)
   throw Error("invalid_envelope_signature");
 let accepted=false;
 try{
   const publicKey=createPublicKey(keys[claims.key_id]);
   if(publicKey.asymmetricKeyType!=="ed25519")throw Error("wrong_key_type");
   accepted=verify(null,envelopeSigningBytes(claims),publicKey,sig);
 }catch{throw Error("invalid_envelope_public_key_or_signature")}
 if(!accepted)throw Error("invalid_envelope_signature");
 return claims;
}